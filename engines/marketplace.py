"""
Engine 1 — Marketplace Engine
Takes a natural language lab goal and returns 3 cost-optimized strategies as JSON.
Each strategy includes cost breakdown, estimated duration, and recommended labware.
"""
import json
import os
from anthropic import Anthropic
from utils.models import MarketplaceResult, Strategy, CostBreakdown, TipCost, ReagentCost, MachineTimeCost, FailureRisk

client = Anthropic()

SYSTEM_PROMPT = """You are IteraMarket, an expert lab automation cost optimizer for Opentrons cloud labs.

Your job: given a scientist's natural language experiment goal, output EXACTLY 3 distinct protocol strategies.
Each strategy must optimize for a different tradeoff: speed, cost, or accuracy.

STRICT OUTPUT FORMAT — return ONLY valid JSON, no preamble, no markdown, no backticks.

{
  "goal": "<restate the goal concisely>",
  "strategies": [
    {
      "id": 1,
      "name": "<short strategy name>",
      "description": "<2-sentence description of approach>",
      "labware": "<specific labware: plate format, pipette type, modules>",
      "estimated_duration_min": <integer>,
      "recommended": <true for the best overall tradeoff, false for others>,
      "cost": {
        "tips": { "count": <int>, "usd": <float> },
        "reagents": { "total_ul": <float>, "usd": <float> },
        "machine_time": { "minutes": <float>, "usd": <float> },
        "failure_risk": { "score": <0.0-1.0>, "flags": ["<risk description>"] },
        "total_estimated_usd": <float>
      }
    }
  ]
}

COST GUIDELINES (use these rates):
- Tips: $0.18 each (200µL standard), $0.35 each (1000µL)
- Machine time: $0.35/minute
- Reagents: enzymes $0.08/µL, antibodies $0.12/µL, buffers $0.001/µL, general $0.004/µL
- Failure risk score: 0.0 = no risk, 1.0 = high risk; flag specific issues

STRATEGY ARCHETYPES — always generate these 3 types:
1. "Fast & Dirty" — maximize throughput, accept slightly higher cost/risk
2. "Balanced" — optimal cost/time tradeoff (this is usually recommended: true)
3. "Precision" — minimize failure risk, accept higher cost/time

For each strategy, vary: plate format (96 vs 384 well), pipette choice (single vs 8-channel vs 96-channel),
whether to use thermocycler/magnetic module, tip reuse policy, and reagent volumes.

Make costs realistic and differentiated — strategy costs should differ by at least 20%.
"""


def run_marketplace(goal: str, on_token=None) -> MarketplaceResult:
    """
    Call the LLM to generate 3 strategies for the given lab goal.
    on_token: optional callback(str) for streaming display in TUI.
    Returns a validated MarketplaceResult.
    """
    raw_json = ""

    with client.messages.stream(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Lab goal: {goal}"}],
    ) as stream:
        for text in stream.text_stream:
            raw_json += text
            if on_token:
                on_token(text)

    # Strip any accidental markdown fences
    raw_json = raw_json.strip()
    if raw_json.startswith("```"):
        raw_json = raw_json.split("```")[1]
        if raw_json.startswith("json"):
            raw_json = raw_json[4:]

    data = json.loads(raw_json.strip())

    strategies = []
    for s in data["strategies"]:
        c = s["cost"]
        cost = CostBreakdown(
            tips=TipCost(**c["tips"]),
            reagents=ReagentCost(**c["reagents"]),
            machine_time=MachineTimeCost(**c["machine_time"]),
            failure_risk=FailureRisk(**c["failure_risk"]),
            total_estimated_usd=c["total_estimated_usd"],
        )
        strategies.append(Strategy(
            id=s["id"],
            name=s["name"],
            description=s["description"],
            labware=s["labware"],
            estimated_duration_min=s["estimated_duration_min"],
            cost=cost,
            recommended=s.get("recommended", False),
        ))

    return MarketplaceResult(goal=data["goal"], strategies=strategies)
