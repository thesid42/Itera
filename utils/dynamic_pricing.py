"""
Dynamic Pricing Engine — Itera
Estimates protocol-specific reagent costs by reasoning over a materials list.

Design philosophy:
  - There is no real-time API for lab reagent pricing.
  - The best ground truth is a combination of:
      1. Verified baseline rates from supplier catalogs (stored in config.yaml)
         used as anchors so the LLM cannot hallucinate wildly.
      2. An LLM estimation pass that receives the actual materials list from the
         RAG (fetched from protocols.io) and produces a per-reagent cost table,
         constrained by the baseline anchors.
  - The returned PricingContext object augments the marketplace engine's
    CostBreakdown so strategies show realistic, protocol-specific costs.

Pricing anchor data (April 2026, US market):
  Sources:
    - Opentrons tip pricing: opentrons.com ($850 / 9600-tip case = $0.089/tip)
    - NEB Q5 PCR Master Mix 2×: ~$0.09/µL (NEB #M0492)
    - ELISA primary antibody (standard): $0.10–$0.40/µL (Abcam, R&D Systems)
    - Secondary antibody (HRP-conjugated): $0.05–$0.15/µL
    - PBS/Tris/saline buffers: ~$0.001/µL
    - Magnetic beads (SPRI/AMPure): $0.02–$0.05/µL equivalent
    - Lysis buffer: $0.002–$0.005/µL
    - Machine time: estimated from Opentrons OT-2 amortized cost + cloud lab
      operator overhead. Range: $0.20–$0.50/min. Default: $0.30/min.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional
from utils.config_loader import get_pricing

logger = logging.getLogger(__name__)


# ── Baseline anchor rates ─────────────────────────────────────────────────────
# These are calibrated from real supplier data (see module docstring).
# They serve as caps and sanity-check anchors for the LLM estimator.
BASELINE_REAGENT_RATES: dict[str, float] = {
    # $/µL — core categories
    "enzyme":                  0.09,   # NEB Q5 PCR master mix benchmark
    "high_fidelity_polymerase": 0.14,  # HiFi / Phusion — premium enzymes
    "ligase":                  0.10,
    "antibody_primary":        0.25,   # research-grade primary Ab
    "antibody_secondary":      0.08,   # HRP/biotin secondary Ab
    "antibody_elisa":          0.12,   # ELISA-optimized antibody pair
    "magnetic_beads":          0.03,   # SPRI/AMPure per µL equivalent
    "lysis_buffer":            0.003,
    "wash_buffer":             0.001,
    "buffer":                  0.001,  # PBS, Tris, saline
    "elution_buffer":          0.002,
    "ethanol":                 0.0005,
    "tmb_substrate":           0.015,  # TMB for ELISA colorimetric step
    "stop_solution":           0.008,
    "media":                   0.002,  # cell culture media
    "default":                 0.005,
}

BASELINE_TIP_RATES: dict[str, float] = {
    "20ul":  0.089,   # Opentrons 20µL tip — $850/9600 case
    "300ul": 0.089,   # Opentrons 300µL/200µL tip — same case pricing
    "1000ul": 0.18,   # 1000µL tips — smaller pack, higher unit cost
    "filtered_200ul": 0.13,  # filtered tips premium
    "filtered_20ul":  0.13,
}

BASELINE_MACHINE_RATE: float = 0.30  # $/min — amortized OT-2 + cloud overhead


@dataclass
class PricingContext:
    """
    Protocol-specific pricing context estimated from the RAG materials list.
    Used by the marketplace engine to replace the generic config.yaml rates.
    """
    # Per-µL rates for this specific protocol's reagents
    reagent_rates: dict[str, float] = field(default_factory=lambda: dict(BASELINE_REAGENT_RATES))
    # Per-tip rate (key = tip size category)
    tip_rates: dict[str, float] = field(default_factory=lambda: dict(BASELINE_TIP_RATES))
    # Per-minute machine rate
    machine_rate: float = BASELINE_MACHINE_RATE
    # Human-readable rationale from the LLM estimator
    rationale: str = ""
    # Whether this was LLM-estimated (True) or pure baseline (False)
    is_estimated: bool = False
    # Raw materials list that was analysed
    materials_analysed: list[str] = field(default_factory=list)


def get_baseline_context() -> PricingContext:
    """
    Return a PricingContext using only the calibrated baseline anchor rates.
    Used as fallback when RAG returns no materials or LLM call fails.

    Also updates from config.yaml overrides if the user has set custom pricing.
    """
    cfg = get_pricing()
    rates = dict(BASELINE_REAGENT_RATES)
    tips = dict(BASELINE_TIP_RATES)
    machine = BASELINE_MACHINE_RATE

    # Allow config.yaml overrides to act as user-specific calibration
    if "reagents" in cfg:
        if "enzyme" in cfg["reagents"]:
            rates["enzyme"] = cfg["reagents"]["enzyme"]
        if "antibody" in cfg["reagents"]:
            rates["antibody_primary"] = cfg["reagents"]["antibody"]
            rates["antibody_elisa"] = cfg["reagents"]["antibody"]
        if "buffer" in cfg["reagents"]:
            rates["buffer"] = cfg["reagents"]["buffer"]
        if "default" in cfg["reagents"]:
            rates["default"] = cfg["reagents"]["default"]
    if "machine_time_per_min" in cfg:
        machine = cfg["machine_time_per_min"]
    if "tips" in cfg:
        if "standard_200ul" in cfg["tips"]:
            tips["300ul"] = cfg["tips"]["standard_200ul"]
            tips["20ul"] = cfg["tips"]["standard_200ul"]
        if "large_1000ul" in cfg["tips"]:
            tips["1000ul"] = cfg["tips"]["large_1000ul"]

    return PricingContext(
        reagent_rates=rates,
        tip_rates=tips,
        machine_rate=machine,
        is_estimated=False,
    )


def estimate_from_materials(
    materials: list[str],
    goal: str = "",
    on_token=None,
) -> PricingContext:
    """
    Use the LLM to estimate protocol-specific reagent costs from a materials list.

    The LLM is given:
      - The list of materials/reagents from the retrieved protocol
      - The baseline anchor rates as hard constraints
      - The user's original experiment goal

    It returns a JSON mapping of reagent categories → $/µL estimates,
    grounded by the anchors. This prevents the LLM from hallucinating
    unrealistic prices while still allowing protocol-specific nuance
    (e.g., distinguishing a $0.09/µL standard Taq from a $0.14/µL HiFi enzyme).

    Falls back to baseline context on any error.
    """
    if not materials:
        logger.info("DynamicPricing | no materials provided — using baseline context")
        return get_baseline_context()

    import json
    from utils.openrouter_client import stream_response

    anchors_summary = "\n".join(
        f"  {k}: ${v:.4f}/µL" for k, v in BASELINE_REAGENT_RATES.items()
    )

    materials_str = "\n".join(f"  - {m}" for m in materials[:20])

    system_prompt = (
        "You are a lab cost estimation expert. You are given a list of reagents/materials "
        "from a published lab protocol and must estimate the cost per microliter ($/µL) "
        "for each reagent category used.\n\n"
        "HARD CONSTRAINTS:\n"
        "- Your estimates MUST be grounded in these real supplier catalog anchor rates:\n"
        f"{anchors_summary}\n\n"
        "- Output ONLY valid JSON — no markdown, no explanation, no backticks.\n"
        "- Use the categories: enzyme, antibody_primary, antibody_secondary, antibody_elisa, "
        "magnetic_beads, lysis_buffer, wash_buffer, buffer, ethanol, tmb_substrate, "
        "stop_solution, media, default\n"
        "- Only include categories actually relevant to this protocol's materials\n"
        "- machine_rate_per_min should reflect protocol complexity (range: 0.20–0.50)\n\n"
        "EXACT OUTPUT FORMAT:\n"
        "{\n"
        '  "reagent_rates": {"enzyme": 0.09, "buffer": 0.001, ...},\n'
        '  "machine_rate_per_min": 0.30,\n'
        '  "rationale": "One sentence explaining the dominant cost driver"\n'
        "}"
    )

    user_message = (
        f"Experiment goal: {goal}\n\n"
        f"Materials/reagents from retrieved protocol:\n{materials_str}\n\n"
        "Estimate the reagent cost rates for this specific protocol."
    )

    try:
        raw = stream_response(
            system_prompt=system_prompt,
            messages=[{"role": "user", "content": user_message}],
            max_tokens=600,
            on_token=on_token,
        )
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        data = json.loads(raw.strip())

        # Merge LLM estimates with baseline anchors
        # LLM output overrides baseline for known categories; baseline fills the rest
        base = get_baseline_context()
        estimated_rates = dict(base.reagent_rates)
        llm_rates = data.get("reagent_rates", {})

        for category, rate in llm_rates.items():
            if isinstance(rate, (int, float)) and rate > 0:
                # Cap at 5× the baseline to prevent hallucinated outliers
                baseline_ref = BASELINE_REAGENT_RATES.get(category, BASELINE_REAGENT_RATES["default"])
                capped = min(float(rate), baseline_ref * 5.0)
                estimated_rates[category] = round(capped, 5)
                if capped != float(rate):
                    logger.warning(
                        "DynamicPricing | capped LLM rate for '%s': $%.4f → $%.4f (5× baseline cap)",
                        category, rate, capped,
                    )

        machine_rate = float(data.get("machine_rate_per_min", base.machine_rate))
        # Cap machine rate between $0.10 and $2.00/min
        machine_rate = max(0.10, min(machine_rate, 2.00))

        rationale = str(data.get("rationale", ""))
        logger.info(
            "DynamicPricing | LLM estimated %d rate(s) | machine=$%.2f/min | %s",
            len(llm_rates), machine_rate, rationale,
        )

        return PricingContext(
            reagent_rates=estimated_rates,
            tip_rates=base.tip_rates,
            machine_rate=machine_rate,
            rationale=rationale,
            is_estimated=True,
            materials_analysed=materials,
        )

    except Exception as exc:
        logger.warning("DynamicPricing | LLM estimation failed (%s) — using baseline", exc)
        return get_baseline_context()


def get_tip_rate(ctx: PricingContext, tip_size_ul: float) -> float:
    """
    Return the per-tip cost for a given tip volume from the pricing context.
    Matches the closest size category.
    """
    if tip_size_ul <= 20:
        return ctx.tip_rates.get("20ul", BASELINE_TIP_RATES["20ul"])
    elif tip_size_ul <= 300:
        return ctx.tip_rates.get("300ul", BASELINE_TIP_RATES["300ul"])
    else:
        return ctx.tip_rates.get("1000ul", BASELINE_TIP_RATES["1000ul"])


def get_reagent_rate(ctx: PricingContext, reagent_name: str) -> float:
    """
    Map a reagent name string to a $/µL rate from the pricing context.
    Uses keyword matching against the reagent_rates categories.
    """
    name = reagent_name.lower()
    # Priority order matters — more specific first
    checks = [
        (["polymerase", "pcr master", "q5", "phusion", "taq"], "enzyme"),
        (["ligase"], "ligase"),
        (["kinase"], "enzyme"),
        (["primary antibody", "anti-"], "antibody_primary"),
        (["secondary antibody", "hrp", "biotin"], "antibody_secondary"),
        (["antibody", "ab ", "igg", "elisa"], "antibody_elisa"),
        (["magnetic bead", "ampure", "spri", "dynabeads"], "magnetic_beads"),
        (["lysis"], "lysis_buffer"),
        (["ethanol", "isopropanol"], "ethanol"),
        (["tmb", "substrate"], "tmb_substrate"),
        (["stop", "h2so4", "sulfuric"], "stop_solution"),
        (["wash", "pbs", "tris", "saline", "bsa"], "wash_buffer"),
        (["elution"], "elution_buffer"),
        (["buffer"], "buffer"),
        (["media", "dmem", "rpmi", "fbs"], "media"),
    ]
    for keywords, category in checks:
        if any(kw in name for kw in keywords):
            return ctx.reagent_rates.get(category, ctx.reagent_rates.get("default", 0.005))
    return ctx.reagent_rates.get("default", 0.005)
