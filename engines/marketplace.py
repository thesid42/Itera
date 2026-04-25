"""
Engine 1 — Marketplace Engine
Takes a natural language lab goal and returns 3 cost-optimized strategies as JSON.

Enhancements over base version:
  1. Bio Knowledge RAG: queries protocols.io before the LLM call so strategies
     are grounded in real published protocols (when token is available).
  2. Cost Model from config: all pricing rates loaded from config.yaml so
     lab managers can update rates without touching code.
  3. LLM backend: OpenRouter (Qwen3) via utils/openrouter_client.py.
"""
import json
import logging
from utils.models import MarketplaceResult, Strategy, CostBreakdown, TipCost, ReagentCost, MachineTimeCost, FailureRisk
from utils.config_loader import get_pricing, get_llm_config
from utils.bio_rag import retrieve_context
from utils.openrouter_client import stream_response

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """You are IteraMarket, an expert lab automation cost optimizer for Opentrons cloud labs.

Your job: given a scientist's natural language experiment goal, output EXACTLY 3 distinct protocol strategies.
Each strategy must optimize for a different tradeoff: speed, cost, or accuracy.

STRICT OUTPUT FORMAT — return ONLY valid JSON, no preamble, no markdown, no backticks.

{{
  "goal": "<restate the goal concisely>",
  "strategies": [
    {{
      "id": 1,
      "name": "<short strategy name>",
      "description": "<2-sentence description of approach>",
      "labware": "<specific labware: plate format, pipette type, modules>",
      "estimated_duration_min": <integer>,
      "recommended": <true for the best overall tradeoff, false for others>,
      "cost": {{
        "tips": {{ "count": <int>, "usd": <float> }},
        "reagents": {{ "total_ul": <float>, "usd": <float> }},
        "machine_time": {{ "minutes": <float>, "usd": <float> }},
        "failure_risk": {{ "score": <0.0-1.0>, "flags": ["<risk description>"] }},
        "total_estimated_usd": <float>
      }}
    }}
  ]
}}

COST GUIDELINES (use these exact rates from lab config):
- Tips: ${tip_200:.2f} each (200µL standard), ${tip_1000:.2f} each (1000µL)
- Machine time: ${machine:.2f}/minute
- Reagents: enzymes ${enzyme:.3f}/µL, antibodies ${antibody:.3f}/µL, buffers ${buffer:.4f}/µL, general ${default:.4f}/µL
- Failure risk score: 0.0 = no risk, 1.0 = high risk; flag specific issues

STRATEGY ARCHETYPES — always generate these 3 types:
1. "Fast & Dirty" — maximize throughput, accept slightly higher cost/risk
2. "Balanced" — optimal cost/time tradeoff (this is usually recommended: true)
3. "Precision" — minimize failure risk, accept higher cost/time

For each strategy, vary: plate format (96 vs 384 well), pipette choice (single vs 8-channel vs 96-channel),
whether to use thermocycler/magnetic module, tip reuse policy, and reagent volumes.

Make costs realistic and differentiated — strategy costs should differ by at least 20%.
"""


def _build_system_prompt() -> str:
    p = get_pricing()
    return _SYSTEM_PROMPT_TEMPLATE.format(
        tip_200=p["tips"]["standard_200ul"],
        tip_1000=p["tips"]["large_1000ul"],
        machine=p["machine_time_per_min"],
        enzyme=p["reagents"]["enzyme"],
        antibody=p["reagents"]["antibody"],
        buffer=p["reagents"]["buffer"],
        default=p["reagents"]["default"],
    )


def run_marketplace(goal: str, on_token=None) -> MarketplaceResult:
    """
    Generate 3 cost-optimized strategies for the given lab goal.

    Steps:
      1. RAG lookup — retrieve biological protocol context from protocols.io
      2. Build system prompt with live pricing from config.yaml
      3. Stream LLM response via OpenRouter (Qwen3)
      4. Parse JSON and return validated MarketplaceResult

    on_token: optional callback(str) for real-time streaming display in TUI.
    """
    logger.info("Marketplace | goal=%r", goal)

    # Step 1: Bio knowledge RAG
    logger.debug("Marketplace | step 1: RAG retrieval")
    bio_context = retrieve_context(goal)
    if bio_context:
        logger.info("Marketplace | RAG context injected (%d chars)", len(bio_context))
    else:
        logger.info("Marketplace | no RAG context matched; proceeding with LLM knowledge only")

    user_message = f"Lab goal: {goal}"
    if bio_context:
        user_message = (
            f"Lab goal: {goal}\n\n"
            "RELEVANT BIOLOGICAL PROTOCOL CONTEXT "
            "(incorporate these constraints into your strategies):\n"
            f"{bio_context}"
        )

    # Step 2 + 3: Build prompt with config pricing and stream
    logger.debug("Marketplace | step 2: building system prompt with config pricing")
    system_prompt = _build_system_prompt()
    cfg = get_llm_config()

    logger.debug("Marketplace | step 3: streaming LLM response")
    raw_json = stream_response(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=cfg["max_tokens"]["marketplace"],
        on_token=on_token,
    )

    # Step 4: Parse and validate
    logger.debug("Marketplace | step 4: parsing JSON response (%d chars)", len(raw_json))
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

    result = MarketplaceResult(goal=data["goal"], strategies=strategies)
    logger.info(
        "Marketplace | done | %d strategies generated | costs: %s",
        len(strategies),
        [f"${s.cost.total_estimated_usd:.2f}" for s in strategies],
    )
    return result
