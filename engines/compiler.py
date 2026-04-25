"""
Engine 2 — Compiler Engine
Translates a chosen Strategy into raw Opentrons API v2 Python code.
Uses few-shot examples in the system prompt to minimize hallucinations.
Supports iterative re-compilation when fed simulator error logs.
"""
from anthropic import Anthropic
from utils.models import Strategy, CompilerResult

client = Anthropic()

SYSTEM_PROMPT = """You are IteraCompiler, an expert Opentrons Python API v2 code generator.

You translate lab protocol strategies into VALID, RUNNABLE Opentrons API v2 Python code.

OUTPUT RULES:
- Output ONLY raw Python code — no markdown, no backticks, no explanation
- Always start with: from opentrons import protocol_api
- Always include the metadata dict and run(protocol: protocol_api.ProtocolContext) function
- Use only labware names that exist in the Opentrons labware library
- Never use deprecated API v1 syntax

CORRECT OPENTRONS V2 SYNTAX EXAMPLES:

# Correct labware loading:
plate = protocol.load_labware('corning_96_wellplate_360ul_flat', '1')
tiprack = protocol.load_labware('opentrons_96_tiprack_300ul', '2')
p300 = protocol.load_instrument('p300_single_gen2', 'right', tip_racks=[tiprack])

# Correct location syntax (slot is a STRING):
module = protocol.load_module('temperature module gen2', '3')  # slot as string

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

COMMON VALID LABWARE NAMES:
- corning_96_wellplate_360ul_flat
- nest_96_wellplate_100ul_pcr_full_skirt
- nest_384_wellplate_100ul_flat
- opentrons_96_tiprack_300ul
- opentrons_96_tiprack_20ul
- opentrons_96_filtertiprack_200ul
- opentrons_6_tuberack_falcon_50ml_conical
- agilent_1_reservoir_290ml
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
        user_message = f"""The following Opentrons v2 code has a simulation error.
Fix ONLY the error. Output corrected Python code only — no explanation.

ERROR FROM SIMULATOR:
{error_log.strip()}

BROKEN CODE:
{previous_code.strip()}
"""
    else:
        user_message = f"""Generate Opentrons API v2 Python code for this protocol strategy:

Strategy name: {strategy.name}
Description: {strategy.description}
Labware to use: {strategy.labware}
Estimated duration: {strategy.estimated_duration_min} minutes

Cost optimization targets:
- Use at most {strategy.cost.tips.count} tips
- Total reagent volume target: ~{strategy.cost.reagents.total_ul:.0f} µL
- Minimize idle robot time

Generate complete, runnable protocol code.
"""

    code = ""
    with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    ) as stream:
        for text in stream.text_stream:
            code += text
            if on_token:
                on_token(text)

    # Strip markdown fences if LLM wrapped output
    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return CompilerResult(strategy=strategy, python_code=code.strip(), attempt=attempt)
