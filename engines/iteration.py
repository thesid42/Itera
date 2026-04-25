"""
Engine 3 — Iteration Engine
Autonomous simulation + error-correction loop.

Workflow:
1. Save generated code to a temp .py file
2. Run: opentrons_simulate <file>
3. If stderr is non-empty → extract error → send back to Compiler Engine
4. Repeat until stderr is empty OR max_attempts reached
5. Compute unified diff between attempts for the TUI
6. Run final cost analysis on the verified code
"""
import subprocess
import tempfile
import os
import difflib
from utils.models import CompilerResult, SimulationAttempt, IterationResult
from utils.cost_analyzer import analyze_cost
from engines.compiler import compile_strategy

MAX_ATTEMPTS = 5


def _run_simulator(code: str) -> tuple[bool, str]:
    """
    Write code to a temp file and run opentrons_simulate.
    Returns (passed: bool, stderr: str).
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, prefix="itera_"
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["opentrons_simulate", tmp_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        stderr = result.stderr.strip()
        # opentrons_simulate exits 0 on success; non-empty stderr = error
        passed = result.returncode == 0 and len(stderr) == 0
        return passed, stderr
    except FileNotFoundError:
        # opentrons_simulate not installed — use dry-run mode
        return _dry_run_simulate(code)
    except subprocess.TimeoutExpired:
        return False, "Simulation timed out after 60 seconds"
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _dry_run_simulate(code: str) -> tuple[bool, str]:
    """
    Fallback when opentrons_simulate is not installed.
    Performs static analysis checks on the code.
    """
    errors = []

    # Check 1: Valid Python syntax
    import ast
    try:
        ast.parse(code)
    except SyntaxError as e:
        errors.append(f"SyntaxError: {e.msg} at line {e.lineno}")
        return False, "\n".join(errors)

    # Check 2: Required structure
    if "def run(" not in code and "def run (" not in code:
        errors.append("ProtocolError: Missing required run(protocol) function")

    if "protocol_api" not in code:
        errors.append("ImportError: Missing 'from opentrons import protocol_api'")

    if "metadata" not in code:
        errors.append("ProtocolWarning: Missing metadata dict (recommended)")

    # Check 3: Deprecated API patterns
    deprecated = [
        ("robot.", "robot module is API v1 — use protocol context instead"),
        ("from opentrons import robot", "Direct robot import is API v1"),
        ("time.sleep", "Use protocol.delay() instead of time.sleep()"),
        (".transfer(", "transfer() method not available in all API v2 versions — use explicit pick_up/aspirate/dispense"),
    ]
    for pattern, msg in deprecated:
        if pattern in code:
            errors.append(f"APIError: {msg}")

    # Check 4: Slot conflicts (same slot used twice)
    import re
    slot_pattern = re.findall(r"load_(?:labware|module|instrument)\([^)]+[,\s]+['\"](\d+)['\"]", code)
    seen_slots = {}
    for slot in slot_pattern:
        if slot in seen_slots:
            errors.append(f"DeckConflictError: Slot {slot} is loaded more than once")
        seen_slots[slot] = True

    if errors:
        return False, "\n".join(errors)
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
    on_attempt=None,
    on_token=None,
) -> IterationResult:
    """
    Main iteration loop.
    on_attempt: callback(SimulationAttempt) — called after each sim run for TUI updates
    on_token: callback(str) — called during LLM recompilation for streaming display
    """
    attempts: list[SimulationAttempt] = []
    current_code = compiler_result.python_code
    previous_code = ""

    for attempt_num in range(1, MAX_ATTEMPTS + 1):
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
            # Success — analyze cost on verified code
            final_cost = analyze_cost(current_code)
            return IterationResult(
                final_code=current_code,
                attempts=attempts,
                total_attempts=attempt_num,
                success=True,
                cost=final_cost,
            )

        if attempt_num == MAX_ATTEMPTS:
            break

        # Failed — recompile with error context
        previous_code = current_code
        fixed = compile_strategy(
            strategy=compiler_result.strategy,
            previous_code=current_code,
            error_log=stderr,
            attempt=attempt_num + 1,
            on_token=on_token,
        )
        current_code = fixed.python_code

    # Exhausted attempts — return best effort
    final_cost = analyze_cost(current_code)
    return IterationResult(
        final_code=current_code,
        attempts=attempts,
        total_attempts=MAX_ATTEMPTS,
        success=False,
        cost=final_cost,
    )
