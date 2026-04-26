"""
Engine 1 — Marketplace Engine
Takes a natural language lab goal and returns 3 cost-optimized strategies as JSON.

Enhancements over base version:
  1. Bio Knowledge RAG: queries protocols.io before the LLM call so strategies
     are grounded in real published protocols (when token is available).
  2. Dynamic Pricing: materials extracted from the RAG are passed to the
     LLM-based pricing estimator, which produces protocol-specific cost rates
     anchored to verified supplier catalog data.
  3. LLM backend: OpenRouter (Qwen3) via utils/openrouter_client.py.
"""
import json
import logging
from utils.models import MarketplaceResult, Strategy, CostBreakdown, TipCost, ReagentCost, MachineTimeCost, FailureRisk
from utils.config_loader import get_llm_config
from utils.bio_rag import retrieve_context
from utils.openrouter_client import stream_response
from utils.dynamic_pricing import (
    PricingContext,
    get_baseline_context,
    estimate_from_materials,
)
from utils.protocols_io import retrieve_protocols

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


def _build_system_prompt(ctx: PricingContext) -> str:
    """
    Build the marketplace system prompt with pricing rates from the
    dynamic pricing context (protocol-specific or baseline fallback).
    """
    # Use the most relevant antibody rate for the prompt
    antibody_rate = max(
        ctx.reagent_rates.get("antibody_primary", 0.25),
        ctx.reagent_rates.get("antibody_elisa", 0.12),
    )
    tip_200 = ctx.tip_rates.get("300ul", 0.089)
    tip_1000 = ctx.tip_rates.get("1000ul", 0.18)
    return _SYSTEM_PROMPT_TEMPLATE.format(
        tip_200=tip_200,
        tip_1000=tip_1000,
        machine=ctx.machine_rate,
        enzyme=ctx.reagent_rates.get("enzyme", 0.09),
        antibody=antibody_rate,
        buffer=ctx.reagent_rates.get("buffer", 0.001),
        default=ctx.reagent_rates.get("default", 0.005),
    )


def run_marketplace(goal: str, on_token=None) -> MarketplaceResult:
    """
    Generate 3 cost-optimized strategies for the given lab goal.

    Steps:
      1. RAG lookup — retrieve protocols from protocols.io (structured objects)
      2. Dynamic pricing — LLM estimates protocol-specific reagent rates from
         the materials list extracted from the fetched protocols
      3. Build system prompt injecting both the bio context and the live rates
      4. Stream LLM response via OpenRouter
      5. Parse JSON and return validated MarketplaceResult

    on_token: optional callback(str) for real-time streaming display in TUI.
    """
    logger.info("Marketplace | goal=%r", goal)
    cfg = get_llm_config()
    from utils.config_loader import get_rag_config
    rag_cfg = get_rag_config()

    # Step 1: Fetch structured protocol objects (not just the text context)
    logger.debug("Marketplace | step 1: structured RAG retrieval")
    protocols = retrieve_protocols(
        goal,
        max_results=rag_cfg.get("protocols_io_max", 3),
        detail_results=rag_cfg.get("protocols_io_detail_max", 2),
    )

    # Aggregate all materials across retrieved protocols for pricing
    all_materials: list[str] = []
    for p in protocols:
        all_materials.extend(p.materials)
    # De-dupe while preserving order
    seen: set[str] = set()
    unique_materials = [m for m in all_materials if not (m in seen or seen.add(m))]

    # Step 2: Dynamic pricing — estimate rates from the actual materials list
    logger.debug("Marketplace | step 2: dynamic pricing estimation | %d unique materials", len(unique_materials))
    if unique_materials:
        pricing_ctx = estimate_from_materials(
            materials=unique_materials,
            goal=goal,
            # Don't stream pricing tokens to TUI — they'd pollute the display
            on_token=None,
        )
        if pricing_ctx.is_estimated:
            logger.info(
                "Marketplace | dynamic pricing | %s",
                pricing_ctx.rationale or "no rationale",
            )
    else:
        pricing_ctx = get_baseline_context()
        logger.info("Marketplace | no materials from RAG — using calibrated baseline pricing")

    # Step 3: Build bio context text for the LLM user message
    from utils.protocols_io import format_protocols_for_rag
    bio_context = ""
    if protocols:
        bio_context = "── Live protocols from protocols.io ──\n" + format_protocols_for_rag(protocols)
        logger.info("Marketplace | RAG context injected (%d chars)", len(bio_context))
    else:
        logger.info("Marketplace | no RAG context — proceeding with LLM knowledge only")

    # Pricing rationale footnote for the LLM
    pricing_note = ""
    if pricing_ctx.is_estimated and pricing_ctx.rationale:
        pricing_note = f"\n\nPRICING NOTE: {pricing_ctx.rationale}"

    user_message = f"Lab goal: {goal}{pricing_note}"
    if bio_context:
        user_message = (
            f"Lab goal: {goal}{pricing_note}\n\n"
            "RELEVANT BIOLOGICAL PROTOCOL CONTEXT "
            "(incorporate these constraints and reagent requirements into your strategies):\n"
            f"{bio_context}"
        )

    # Step 4: Build system prompt with dynamic rates and stream LLM
    logger.debug("Marketplace | step 4: building prompt | machine=$%.2f/min | enzyme=$%.4f/uL",
                 pricing_ctx.machine_rate, pricing_ctx.reagent_rates.get("enzyme", 0.09))
    system_prompt = _build_system_prompt(pricing_ctx)

    logger.debug("Marketplace | step 4b: streaming LLM response")
    raw_json = stream_response(
        system_prompt=system_prompt,
        messages=[{"role": "user", "content": user_message}],
        max_tokens=cfg["max_tokens"]["marketplace"],
        temperature=cfg.get("temperature", {}).get("marketplace", 0.7),
        on_token=on_token,
    )

    # Step 5: Parse and validate
    logger.debug("Marketplace | step 5: parsing JSON response (%d chars)", len(raw_json))
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
        "Marketplace | done | %d strategies | costs: %s | pricing_source: %s",
        len(strategies),
        [f"${s.cost.total_estimated_usd:.2f}" for s in strategies],
        "dynamic" if pricing_ctx.is_estimated else "baseline",
    )
    return result
