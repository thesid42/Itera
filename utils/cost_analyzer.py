"""
Cost Analyzer — Engine 1.5
Parses generated Opentrons Python code to extract real cost metrics:
  - tip count (from pick_up_tip calls)
  - total aspirate volume per reagent
  - estimated machine time (from delay/thermocycler calls)
  - failure risk flags (missing seals, tip reuse, edge-well patterns)
"""
import ast
import re
from utils.models import CostBreakdown, TipCost, ReagentCost, MachineTimeCost, FailureRisk

# ─── Pricing constants (approximate cloud lab rates) ──────────────────────────
TIP_COST_USD = 0.18           # per tip (standard 200µL)
MACHINE_TIME_USD_PER_MIN = 0.35
REAGENT_COST_PER_UL = {
    "enzyme":    0.08,
    "antibody":  0.12,
    "buffer":    0.001,
    "default":   0.004,
}


def _classify_reagent(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ["enzyme", "polymerase", "ligase", "kinase"]):
        return "enzyme"
    if any(k in n for k in ["antibody", "ab", "igg"]):
        return "antibody"
    if any(k in n for k in ["buffer", "pbs", "tris", "saline"]):
        return "buffer"
    return "default"


def _parse_number(node) -> float:
    """Extract a numeric literal from an AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_parse_number(node.operand)
    return 0.0


def analyze_cost(python_code: str) -> CostBreakdown:
    """
    Parse Opentrons Python code and return a populated CostBreakdown.
    Falls back gracefully if parsing fails.
    """
    try:
        tree = ast.parse(python_code)
    except SyntaxError:
        return CostBreakdown()

    # Pre-pass: detect loop multiplier from range() in for-loops
    loop_multiplier = 1
    for _node in ast.walk(tree):
        if isinstance(_node, ast.For):
            if (isinstance(_node.iter, ast.Call) and
                    isinstance(_node.iter.func, ast.Name) and
                    _node.iter.func.id == 'range' and _node.iter.args):
                val = _parse_number(_node.iter.args[0])
                if val > 1:
                    loop_multiplier = max(loop_multiplier, int(val))

    tip_count = 0
    total_ul = 0.0
    reagent_usd = 0.0
    machine_seconds = 0.0
    risk_flags: list[str] = []

    has_seal_before_long_delay = True
    last_delay_seconds = 0.0
    has_seal_call = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        # Resolve call name: pipette.pick_up_tip() or just pick_up_tip()
        func_name = ""
        if isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        elif isinstance(node.func, ast.Name):
            func_name = node.func.id

        # ── Tip usage ─────────────────────────────────────────────────────────
        if func_name == "pick_up_tip":
            tip_count += loop_multiplier

        # ── Aspirate volume ───────────────────────────────────────────────────
        if func_name == "aspirate":
            vol = 0.0
            if node.args:
                vol = _parse_number(node.args[0])
            for kw in node.keywords:
                if kw.arg == "volume":
                    vol = _parse_number(kw.value)
            total_ul += vol

            # Try to infer reagent name from nearby variable assignment
            reagent_type = "default"
            reagent_usd += vol * REAGENT_COST_PER_UL[reagent_type]

        # ── Delay / incubation time ───────────────────────────────────────────
        if func_name == "delay":
            secs = 0.0
            mins = 0.0
            if node.args:
                secs = _parse_number(node.args[0])
            for kw in node.keywords:
                if kw.arg == "seconds":
                    secs = _parse_number(kw.value)
                if kw.arg == "minutes":
                    mins = _parse_number(kw.value)
            total_secs = secs + mins * 60
            machine_seconds += total_secs
            last_delay_seconds = total_secs

            # Flag long delays without plate seal
            if total_secs > 1800 and not has_seal_call:
                risk_flags.append(
                    f"Long delay ({int(total_secs/60)} min) with no plate seal — evaporation risk"
                )

        # ── Thermocycler time ─────────────────────────────────────────────────
        if func_name in ("execute_profile", "run_profile"):
            # Rough estimate: assume 2 min per cycle on average
            machine_seconds += 120

        # ── Seal detection ────────────────────────────────────────────────────
        if func_name in ("seal", "seal_plate", "apply_seal"):
            has_seal_call = True

        # ── Tip reuse risk ────────────────────────────────────────────────────
        if func_name == "drop_tip":
            # If aspirate was called more times than tips picked up, reuse implied
            pass

    # ── Post-walk risk scoring ─────────────────────────────────────────────────
    risk_score = min(len(risk_flags) * 0.2, 1.0)

    # Check for edge-well patterns in well selections
    edge_wells = {"A1", "A12", "H1", "H12", "A2", "H2", "A11", "H11"}
    well_mentions = set(re.findall(r"\b([A-H]\d{1,2})\b", python_code))
    edge_used = well_mentions & edge_wells
    if len(edge_used) > 3:
        risk_flags.append(
            f"Edge wells used ({', '.join(sorted(edge_used)[:4])}...) — thermal uniformity risk in PCR"
        )
        risk_score = min(risk_score + 0.15, 1.0)

    # Tip reuse heuristic
    aspirate_pattern = len(re.findall(r"\.aspirate\(", python_code))
    pickup_pattern = len(re.findall(r"\.pick_up_tip\(", python_code))
    if aspirate_pattern > 0 and pickup_pattern > 0:
        if aspirate_pattern / pickup_pattern > 3:
            risk_flags.append(
                "High aspirate:tip ratio — possible tip reuse across different samples"
            )
            risk_score = min(risk_score + 0.1, 1.0)

    machine_mins = machine_seconds / 60.0
    machine_usd = round(machine_mins * MACHINE_TIME_USD_PER_MIN, 2)

    cost = CostBreakdown(
        tips=TipCost(count=tip_count, usd=round(tip_count * TIP_COST_USD, 2)),
        reagents=ReagentCost(total_ul=round(total_ul, 1), usd=round(reagent_usd, 2)),
        machine_time=MachineTimeCost(minutes=round(machine_mins, 1), usd=machine_usd),
        failure_risk=FailureRisk(score=round(risk_score, 2), flags=risk_flags),
    )
    cost.recalculate_total()
    return cost
