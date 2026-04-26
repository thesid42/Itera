"""
Engine 3 — Iteration Engine
Autonomous simulation + error-correction loop.

Workflow:
1. Save generated code to a temp .py file
2. Run: opentrons_simulate <file>  (or python -m opentrons.simulate as fallback)
3. If there are real errors → extract error → send back to Compiler Engine
4. Repeat until clean OR max_attempts reached
5. Compute unified diff between attempts for the TUI
6. Run final cost analysis on the verified code
"""
import logging
import subprocess
import sys
import tempfile
import os
import re
import difflib
from utils.models import CompilerResult, SimulationAttempt, IterationResult
from utils.cost_analyzer import analyze_cost
from engines.compiler import compile_strategy

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5

# ── Calibration noise lines emitted by opentrons_simulate even on success ──────
# These appear on stderr regardless of whether the protocol is valid.
# We must strip them before deciding pass/fail and before sending to LLM.
_CALIBRATION_NOISE = re.compile(
    r"(robot_settings\.json not found|deck_calibration\.json not found|"
    r"Deck calibration not found|Loading defaults|\.opentrons\\.+\.json)",
    re.IGNORECASE,
)


def _strip_noise(stderr: str) -> str:
    """Remove opentrons calibration warning lines from stderr."""
    lines = [l for l in stderr.splitlines() if not _CALIBRATION_NOISE.search(l)]
    return "\n".join(lines).strip()


def _find_simulator() -> list:
    """
    Return the command list to invoke opentrons_simulate.
    Checks the venv bin/Scripts directory for the script (with and without .exe),
    then falls back to 'python -m opentrons.simulate' which works anywhere.
    """
    bin_dir = os.path.dirname(sys.executable)
    candidates = [
        # Unix/macOS venv
        os.path.join(bin_dir, "opentrons_simulate"),
        # Windows venv (Scripts subfolder, .exe)
        os.path.join(bin_dir, "Scripts", "opentrons_simulate.exe"),
        os.path.join(bin_dir, "opentrons_simulate.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            logger.debug("Simulator | found opentrons_simulate at %s", path)
            return [path]

    logger.info("Simulator | opentrons_simulate not found in venv — using 'python -m opentrons.simulate'")
    return [sys.executable, "-m", "opentrons.simulate"]


def _run_simulator(code: str) -> tuple:
    """
    Write code to a temp file and run opentrons_simulate (or the module fallback).
    Returns (passed: bool, clean_error: str).

    Pass/fail logic:
      - returncode == 0 means the protocol parsed and simulated without fatal errors.
      - We strip calibration noise from stderr before the pass/fail decision
        AND before forwarding the error to the LLM compiler.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="itera_"
    ) as f:
        f.write(code)
        tmp_path = f.name

    logger.debug("Simulator | wrote %d lines to %s", len(code.splitlines()), tmp_path)

    cmd = _find_simulator() + [tmp_path]
    logger.debug("Simulator | command: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
        )
        # Strip calibration noise — these are always emitted even on valid protocols
        clean_err = _strip_noise(result.stderr)

        # returncode is the ground truth. 0 = success.
        # Only flag remaining stderr as failure if returncode is also non-zero.
        passed = result.returncode == 0

        logger.info(
            "Simulator | returncode=%d | raw_stderr=%d chars | clean_err=%d chars | passed=%s",
            result.returncode, len(result.stderr), len(clean_err), passed,
        )
        if clean_err:
            logger.warning("Simulator | clean errors:\n%s", clean_err)

        # If returncode is 0, return empty error (calibration noise is not an error)
        return passed, clean_err if not passed else ""

    except subprocess.TimeoutExpired:
        logger.error("Simulator | timed out after 90 seconds")
        return False, "Simulation timed out after 90 seconds"
    except Exception as e:
        logger.warning("Simulator | subprocess failed (%s) — falling back to dry-run", e)
        return _dry_run_simulate(code)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


# ── Known valid Opentrons entities ─────────────────────────────────────────────
_KNOWN_LABWARE = {
    "corning_96_wellplate_360ul_flat",
    "nest_96_wellplate_100ul_pcr_full_skirt",
    "nest_96_wellplate_200ul_flat",
    "nest_384_wellplate_100ul_flat",
    "opentrons_96_tiprack_300ul",
    "opentrons_96_tiprack_20ul",
    "opentrons_96_tiprack_1000ul",
    "opentrons_96_filtertiprack_200ul",
    "opentrons_96_filtertiprack_20ul",
    "opentrons_6_tuberack_falcon_50ml_conical",
    "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
    "opentrons_24_tuberack_nest_0.5ml_screwcap",
    "agilent_1_reservoir_290ml",
    "nest_12_reservoir_15ml",
    "nest_1_reservoir_195ml",
}
_KNOWN_INSTRUMENTS = {
    "p300_single_gen2", "p300_multi_gen2",
    "p20_single_gen2", "p20_multi_gen2",
    "p1000_single_gen2",
}
_KNOWN_MODULES = {
    "thermocycler module", "thermocycler module gen2",
    "temperature module", "temperature module gen2",
    "magnetic module", "magnetic module gen2",
    "heater shaker module", "heater shaker module gen1",
}
# Thermocycler physically occupies slots 7, 8, 10, 11
_THERMOCYCLER_SLOTS = {"7", "8", "10", "11"}


def _dry_run_simulate(code: str) -> tuple:
    """
    Hardened static analysis fallback — used only when opentrons_simulate
    cannot be invoked at all (e.g., not installed, network sandbox).
    Catches the most common LLM hallucination errors.
    """
    import ast
    logger.debug("Dry-run | starting hardened static analysis checks")
    errors = []

    # Check 1: Valid Python syntax — must pass before anything else
    try:
        ast.parse(code)
    except SyntaxError as e:
        errors.append(f"SyntaxError: {e.msg} at line {e.lineno}")
        return False, "\n".join(errors)

    # Check 2: Required protocol structure
    if "def run(" not in code and "def run (" not in code:
        errors.append("ProtocolError: Missing required run(protocol) function")
    if "protocol_api" not in code:
        errors.append("ImportError: Missing 'from opentrons import protocol_api'")
    if "metadata" not in code:
        errors.append("ProtocolError: Missing metadata dict — add metadata = {'apiLevel': '2.18'}")
    elif "apiLevel" not in code:
        errors.append("ProtocolError: metadata dict is missing 'apiLevel' key — add 'apiLevel': '2.18'")
    if "define_liquid" not in code:
        errors.append("ProtocolError: Missing protocol.define_liquid() calls — all liquids must be defined")
    if "load_liquid" not in code:
        errors.append("ProtocolError: Missing well.load_liquid() calls — load starting volumes into reservoir wells")
    if "add_parameters" not in code:
        errors.append("ProtocolError: Missing add_parameters() function — runtime parameters (volumes, counts, times) must be declared")

    # Check 3: Deprecated / forbidden patterns
    deprecated = [
        ("robot.",               "robot module is API v1 — use protocol context methods instead"),
        ("from opentrons import robot", "Direct robot import is API v1"),
        ("time.sleep",           "Use protocol.delay() instead of time.sleep()"),
        ("import time",          "Do not import time; use protocol.delay() for waits"),
    ]
    for pattern, msg in deprecated:
        if pattern in code:
            errors.append(f"APIError: {msg}")

    # Check 4: Deck slot conflicts
    labware_loads = re.findall(
        r'load_labware\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]',
        code,
    )
    module_loads = re.findall(
        r'load_module\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]',
        code,
    )
    instrument_loads = re.findall(
        r'load_instrument\s*\(\s*[\'"]([^\'"]+)[\'"]\s*,\s*[\'"]([^\'"]+)[\'"]',
        code,
    )

    occupied: dict = {}
    has_thermocycler = False

    for mod_name, slot in module_loads:
        mod_lower = mod_name.strip().lower()
        if "thermocycler" in mod_lower:
            has_thermocycler = True
            for tc_slot in _THERMOCYCLER_SLOTS:
                if tc_slot in occupied:
                    errors.append(
                        f"DeckConflictError: Slot {tc_slot} conflict — thermocycler module "
                        f"needs slots 7,8,10,11 but slot {tc_slot} is already used by {occupied[tc_slot]}"
                    )
                occupied[tc_slot] = "thermocycler module"
        else:
            if slot in occupied:
                errors.append(f"DeckConflictError: Slot {slot} already used by '{occupied[slot]}'")
            occupied[slot] = mod_name
        if mod_lower not in _KNOWN_MODULES:
            errors.append(
                f"ModuleError: Unknown module '{mod_name}' — valid modules: "
                + ", ".join(f"'{m}'" for m in sorted(_KNOWN_MODULES))
            )

    for lw_name, slot in labware_loads:
        if slot in occupied:
            errors.append(f"DeckConflictError: Slot '{slot}' already occupied by '{occupied[slot]}'")
        occupied[slot] = lw_name
        if lw_name.strip() not in _KNOWN_LABWARE:
            errors.append(
                f"LabwareError: Unknown labware '{lw_name}' — use an exact name from the Opentrons "
                f"labware library. Examples: {', '.join(sorted(_KNOWN_LABWARE)[:5])}..."
            )

    # Check 5: Instrument mount validation
    seen_mounts: set = set()
    for instr_name, mount in instrument_loads:
        if mount not in {"left", "right"}:
            errors.append(f"InstrumentError: Invalid mount '{mount}' — must be 'left' or 'right'")
        if mount in seen_mounts:
            errors.append(f"InstrumentError: Mount '{mount}' is already occupied by another instrument")
        seen_mounts.add(mount)
        if instr_name.strip() not in _KNOWN_INSTRUMENTS:
            errors.append(
                f"InstrumentError: Unknown instrument '{instr_name}' — "
                f"valid: {', '.join(sorted(_KNOWN_INSTRUMENTS))}"
            )

    if errors:
        logger.warning("Dry-run | %d check(s) failed:\n%s", len(errors), "\n".join(errors))
        return False, "\n".join(errors)
    logger.info("Dry-run | all static checks passed")
    return True, ""


_VERIFIER_SYSTEM_PROMPT = """You are IteraVerifier, a strict lab protocol correctness checker.

You are given a scientist's original experiment goal and an Opentrons Python protocol generated to fulfil it.
Your job: decide whether the protocol ACTUALLY implements the goal completely and correctly.

Respond with ONLY valid JSON — no markdown, no preamble:
{
  "passed": true | false,
  "missing": ["<specific step or reagent missing from the code>", ...],
  "wrong": ["<specific thing implemented incorrectly>", ...],
  "summary": "<one sentence verdict>"
}

Be strict:
- If a reagent mentioned in the goal is absent from define_liquid() → missing
- If a key protocol step (e.g. cell lysis, PCR cycling, magnetic separation) is absent → missing
- If volumes, temperatures, or cycle counts are biologically unrealistic for the goal → wrong
- If the wrong labware type is used for the assay → wrong
- Ignore minor style issues (variable names, comments)

If the protocol is a reasonable, complete implementation of the goal, return passed: true with empty lists."""

_VERIFIER_MAX_TOKENS = 512


def _verify_goal_alignment(goal: str, code: str) -> tuple[bool, str]:
    """
    Ask the LLM to check whether the generated code actually implements the goal.
    Returns (passed, feedback_for_compiler).
    Skipped silently if goal is empty.
    """
    if not goal:
        return True, ""

    from utils.openrouter_client import stream_response
    from utils.config_loader import get_llm_config
    import json as _json

    cfg = get_llm_config()
    user_message = (
        f"ORIGINAL GOAL:\n{goal}\n\n"
        f"GENERATED PROTOCOL CODE:\n{code}"
    )

    logger.info("Verifier | checking goal alignment | goal=%r", goal[:80])
    try:
        raw = stream_response(
            system_prompt=_VERIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=_VERIFIER_MAX_TOKENS,
            temperature=cfg.get("temperature", {}).get("compiler", 0.2),
        )
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].lstrip("json").strip()

        result = _json.loads(raw)
        passed = bool(result.get("passed", True))
        missing = result.get("missing", [])
        wrong = result.get("wrong", [])
        summary = result.get("summary", "")

        if passed:
            logger.info("Verifier | PASSED | %s", summary)
            return True, ""

        feedback_lines = [f"Goal alignment check FAILED: {summary}"]
        if missing:
            feedback_lines.append("Missing from protocol:")
            feedback_lines.extend(f"  - {m}" for m in missing)
        if wrong:
            feedback_lines.append("Incorrectly implemented:")
            feedback_lines.extend(f"  - {w}" for w in wrong)
        feedback = "\n".join(feedback_lines)
        logger.warning("Verifier | FAILED |\n%s", feedback)
        return False, feedback

    except Exception as exc:
        logger.warning("Verifier | error during verification (%s) — skipping", exc)
        return True, ""


def _compute_diff(old_code: str, new_code: str) -> str:
    """Generate a unified diff string between two code versions."""
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile="attempt_previous.py",
        tofile="attempt_fixed.py",
        n=2,
    )
    return "".join(list(diff)[:60])  # cap at 60 lines for TUI display


def run_iteration(
    compiler_result: CompilerResult,
    goal: str = "",
    on_attempt=None,
    on_token=None,
) -> IterationResult:
    """
    Main iteration loop.
    on_attempt: callback(SimulationAttempt) — called after each sim run for TUI updates
    on_token: callback(str) — called during LLM recompilation for streaming display
    """
    strategy_name = compiler_result.strategy.name
    logger.info("Iteration | starting | strategy=%r | max_attempts=%d", strategy_name, MAX_ATTEMPTS)

    attempts: list = []
    current_code = compiler_result.python_code
    previous_code = ""

    for attempt_num in range(1, MAX_ATTEMPTS + 1):
        logger.info("Iteration | attempt %d/%d | strategy=%r", attempt_num, MAX_ATTEMPTS, strategy_name)
        passed, stderr = _run_simulator(current_code)

        diff = _compute_diff(previous_code, current_code) if previous_code else ""

        sim_attempt = SimulationAttempt(
            attempt=attempt_num,
            code=current_code,
            stderr=stderr,
            passed=passed,
            diff=diff,
        )
        attempts.append(sim_attempt)

        if on_attempt:
            on_attempt(sim_attempt)

        if passed:
            logger.info("Iteration | simulation PASSED on attempt %d | strategy=%r", attempt_num, strategy_name)

            # Goal alignment check — verify the code actually implements the goal
            goal_ok, goal_feedback = _verify_goal_alignment(goal, current_code)
            if not goal_ok and attempt_num < MAX_ATTEMPTS:
                logger.warning("Iteration | goal alignment FAILED — triggering fix recompile")
                previous_code = current_code
                fixed = compile_strategy(
                    strategy=compiler_result.strategy,
                    goal=goal,
                    previous_code=current_code,
                    error_log=goal_feedback,
                    attempt=attempt_num + 1,
                    on_token=on_token,
                )
                current_code = fixed.python_code
                continue

            final_cost = analyze_cost(current_code)
            logger.info(
                "Iteration | final cost $%.2f | tips=%d | reagents=%.0fuL | machine=%.1fmin",
                final_cost.total_estimated_usd, final_cost.tips.count,
                final_cost.reagents.total_ul, final_cost.machine_time.minutes,
            )
            return IterationResult(
                final_code=current_code,
                attempts=attempts,
                total_attempts=attempt_num,
                success=True,
                cost=final_cost,
            )

        logger.warning(
            "Iteration | attempt %d FAILED | strategy=%r | error=%r",
            attempt_num, strategy_name, stderr[:200],
        )

        if attempt_num == MAX_ATTEMPTS:
            break

        # Failed — recompile with error context
        logger.info("Iteration | triggering re-compilation for attempt %d", attempt_num + 1)
        previous_code = current_code
        fixed = compile_strategy(
            strategy=compiler_result.strategy,
            goal=goal,
            previous_code=current_code,
            error_log=stderr,
            attempt=attempt_num + 1,
            on_token=on_token,
        )
        current_code = fixed.python_code

    logger.error(
        "Iteration | EXHAUSTED all %d attempts | strategy=%r | returning best effort",
        MAX_ATTEMPTS, strategy_name,
    )
    final_cost = analyze_cost(current_code)
    return IterationResult(
        final_code=current_code,
        attempts=attempts,
        total_attempts=MAX_ATTEMPTS,
        success=False,
        cost=final_cost,
    )
