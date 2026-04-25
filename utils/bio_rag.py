"""
Bio Knowledge RAG — Engine 1 pre-step.

Retrieves relevant protocol context from protocols.io before the marketplace
LLM call, grounding strategies in real published protocols.
"""
import logging
import re
from typing import Optional
from utils.config_loader import get_rag_config

logger = logging.getLogger(__name__)

def _tokenize(text: str) -> set:
    return set(re.findall(r"\b[a-z]{2,}\b", text.lower()))


def retrieve_context(
    goal: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> str:
    """
    Return a formatted string of relevant protocol context to inject into
    the marketplace LLM system prompt.

    Source:
      - protocols.io live API — fetches published community protocols when
        PROTOCOLS_IO_TOKEN is set; degrades gracefully if absent or offline.
    """
    cfg = get_rag_config()
    if top_k is None:
        top_k = cfg.get("protocols_io_max", 3)
    if min_score is None:
        min_score = 0.0

    logger.info("RAG retrieval | goal=%r | protocols_io_max=%d", goal, top_k)

    from utils.protocols_io import retrieve_protocols, format_protocols_for_rag
    detail_max = cfg.get("protocols_io_detail_max", 2)
    prots = retrieve_protocols(goal, max_results=top_k, detail_results=detail_max)
    live_ctx = format_protocols_for_rag(prots) if prots else ""
    if not live_ctx:
        logger.info("RAG retrieval | no live protocols found for goal=%r", goal)
        return ""
    combined = "── Live protocols from protocols.io ──\n" + live_ctx
    logger.info("RAG retrieval | combined context: %d chars (live=%d)", len(combined), len(live_ctx))
    return combined
