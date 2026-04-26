"""
Engine 2 — Compiler Engine
Translates a chosen Strategy into raw Opentrons API v2 Python code.
Uses few-shot examples in the system prompt to minimize hallucinations.
Supports iterative re-compilation when fed simulator error logs.

LLM backend: OpenRouter (Qwen3) via utils/openrouter_client.py.
"""
import logging
from utils.models import Strategy, CompilerResult
from utils.config_loader import get_llm_config
from utils.openrouter_client import stream_response

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are IteraCompiler, an expert Opentrons Python API v2 code generator.

You translate lab protocol strategies into VALID, RUNNABLE Opentrons API v2 Python code.

OUTPUT RULES:
- Output ONLY raw Python code — no markdown, no backticks, no explanation
- Always start with: from opentrons import protocol_api
- Always include the metadata dict with 'apiLevel': '2.18' and the run(protocol: protocol_api.ProtocolContext) function
- Use only labware names that exist in the Opentrons labware library (see list below)
- Never use deprecated API v1 syntax
- ALWAYS define every liquid with protocol.define_liquid() and load starting volumes with well.load_liquid()
- ALWAYS define runtime parameters in an add_parameters(parameters) function at module level
- Parameters must cover at minimum: sample count, key volumes (µL), and incubation times

REQUIRED METADATA FORMAT (always include exactly this):
metadata = {
    'protocolName': '<short descriptive name>',
    'author': 'Itera',
    'description': '<one-line description>',
    'apiLevel': '2.18'
}

CORRECT OPENTRONS V2 SYNTAX EXAMPLES:

# Correct labware loading:
plate = protocol.load_labware('corning_96_wellplate_360ul_flat', '1')
tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', '2')
p300 = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack])

# Correct location syntax (slot is a STRING):
module = protocol.load_module('temperature module gen2', '3')  # slot as string

# REQUIRED — Define every liquid used in the protocol:
sample_liquid   = protocol.define_liquid(name='Sample',          description='Cell lysate or analyte',  display_color='#0077FF')
buffer_liquid   = protocol.define_liquid(name='Wash Buffer',     description='PBS pH 7.4',               display_color='#00BBBB')
reagent_liquid  = protocol.define_liquid(name='Primary Antibody',description='Anti-target Ab, 1:1000',   display_color='#FF6600')
waste_liquid    = protocol.define_liquid(name='Waste',           description='Liquid waste',             display_color='#888888')

# REQUIRED — Load starting volumes into the wells that hold each liquid:
reservoir['A1'].load_liquid(liquid=sample_liquid,  volume=5000)   # µL present at run start
reservoir['A2'].load_liquid(liquid=buffer_liquid,  volume=10000)
reservoir['A3'].load_liquid(liquid=reagent_liquid, volume=2000)

# REQUIRED — Runtime parameters (define BEFORE run(), at module level, using add_parameters):
def add_parameters(parameters: protocol_api.Parameters):
    parameters.add_int(
        variable_name='sample_count',
        display_name='Number of samples',
        description='How many wells to process (1–96)',
        default=96,
        minimum=1,
        maximum=96,
    )
    parameters.add_float(
        variable_name='incubation_time_min',
        display_name='Incubation time (min)',
        description='Primary antibody incubation duration',
        default=30.0,
        minimum=5.0,
        maximum=120.0,
    )
    parameters.add_float(
        variable_name='wash_volume_ul',
        display_name='Wash volume (µL)',
        description='Volume per wash cycle',
        default=200.0,
        minimum=50.0,
        maximum=300.0,
    )

# Inside run(), read parameters via protocol.params:
# sample_count      = protocol.params.sample_count
# incubation_time   = protocol.params.incubation_time_min
# wash_vol          = protocol.params.wash_volume_ul

# Correct transfer:
p300.pick_up_tip()
p300.aspirate(50, plate['A1'])
p300.dispense(50, plate['B1'])
p300.drop_tip()

# Correct delay (use protocol.delay, not time.sleep):
protocol.delay(seconds=30)
protocol.delay(minutes=5, msg='Incubating')

# Correct thermocycler:
tc_mod = protocol.load_module('thermocycler module', '7')
tc_plate = tc_mod.load_labware('nest_96_wellplate_100ul_pcr_full_skirt')
tc_mod.close_lid()
tc_mod.set_lid_temperature(105)
tc_mod.set_block_temperature(95, hold_time_seconds=30)
profile = [
    {'temperature': 95, 'hold_time_seconds': 30},
    {'temperature': 55, 'hold_time_seconds': 30},
    {'temperature': 72, 'hold_time_seconds': 60},
]
tc_mod.execute_profile(steps=profile, repetitions=30, block_max_volume=25)
tc_mod.set_block_temperature(4)
tc_mod.open_lid()

COMMON VALID LABWARE NAMES (use ONLY these exact strings):
Plates:
- corning_96_wellplate_360ul_flat
- nest_96_wellplate_100ul_pcr_full_skirt
- nest_96_wellplate_200ul_flat
- nest_384_wellplate_100ul_flat
Tip racks:
- opentrons_96_tiprack_20ul
- opentrons_96_tiprack_300ul
- opentrons_96_tiprack_1000ul
- opentrons_96_filtertiprack_20ul
- opentrons_96_filtertiprack_200ul
Tube racks:
- opentrons_6_tuberack_falcon_50ml_conical
- opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap
- opentrons_24_tuberack_nest_0.5ml_screwcap
Reservoirs:
- agilent_1_reservoir_290ml
- nest_1_reservoir_195ml
- nest_12_reservoir_15ml

COMMON VALID INSTRUMENT NAMES:
- p300_single_gen2, p300_multi_gen2
- p20_single_gen2, p20_multi_gen2
- p1000_single_gen2

COST OPTIMIZATION RULES to follow:
- Use multi-channel pipettes when processing full rows/columns
- Minimize tip changes: aspirate multiple times with same tip when contamination risk is low
- Batch incubations: group delays together to reduce robot idle time
- Use protocol.delay() for incubations — never import time or use time.sleep()
- Add protocol.comment() calls to describe each step (helps with simulation tracing)
"""


def compile_strategy(
    strategy: Strategy,
    goal: str = "",
    previous_code: str = "",
    error_log: str = "",
    attempt: int = 1,
    on_token=None,
) -> CompilerResult:
    """
    Generate or fix Opentrons Python code for a given strategy.

    If error_log is provided, this is a re-compilation pass:
    the LLM sees the broken code + the simulator error and must fix it.
    """
    if error_log and previous_code:
        logger.info("Compiler | attempt=%d | re-compilation with error context | strategy=%r", attempt, strategy.name)
        logger.debug("Compiler | error_log: %s", error_log[:300])
        user_message = (
            "The following Opentrons v2 code has a simulation error.\n"
            "Fix ONLY the error. Output corrected Python code only — no explanation.\n\n"
            f"ERROR FROM SIMULATOR:\n{error_log.strip()}\n\n"
            f"BROKEN CODE:\n{previous_code.strip()}"
        )
    else:
        logger.info("Compiler | attempt=%d | fresh compilation | strategy=%r", attempt, strategy.name)
        goal_line = f"Original experiment goal: {goal}\n" if goal else ""
        user_message = (
            "Generate Opentrons API v2 Python code for this protocol strategy:\n\n"
            f"{goal_line}"
            f"Strategy name: {strategy.name}\n"
            f"Description: {strategy.description}\n"
            f"Labware to use: {strategy.labware}\n"
            f"Estimated duration: {strategy.estimated_duration_min} minutes\n\n"
            "Cost optimization targets:\n"
            f"- Use at most {strategy.cost.tips.count} tips\n"
            f"- Total reagent volume target: ~{strategy.cost.reagents.total_ul:.0f} µL\n"
            "- Minimize idle robot time\n\n"
            "Generate complete, runnable protocol code."
        )

    cfg = get_llm_config()
    code = stream_response(
        system_prompt=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=cfg["max_tokens"]["compiler"],
        temperature=cfg.get("temperature", {}).get("compiler", 0.2),
        on_token=on_token,
    )

    # Strip markdown fences if the model wrapped output despite instructions
    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    result = CompilerResult(strategy=strategy, python_code=code.strip(), attempt=attempt)
    logger.info("Compiler | attempt=%d | generated %d lines of code", attempt, len(result.python_code.splitlines()))
    return result
