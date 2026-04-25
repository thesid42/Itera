"""
Bio Knowledge RAG — Engine 1 pre-step.

Retrieves relevant biological protocol constraints from the local knowledge base
before the marketplace LLM call, grounding strategies in real lab requirements.

Uses keyword overlap (TF-IDF-style) — no external ML dependencies required.
"""
import json
import logging
import os
import re
from functools import lru_cache
from typing import Optional
from utils.config_loader import get_rag_config

logger = logging.getLogger(__name__)

_KB_PATH = os.path.join(os.path.dirname(__file__), "..", "knowledge", "bio_protocols.json")


@lru_cache(maxsize=1)
def _load_knowledge_base() -> list:
    path = os.path.abspath(_KB_PATH)
    logger.debug("Loading bio knowledge base from %s", path)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    entries = data["protocols"]
    logger.debug("Loaded %d protocol entries from knowledge base", len(entries))
    return entries


def _tokenize(text: str) -> set:
    return set(re.findall(r"\b[a-z]{2,}\b", text.lower()))


def _score(query_tokens: set, entry: dict) -> float:
    """
    Keyword overlap score between query and knowledge base entry.
    Weights: keywords field 2×, name 2×, description 1×, constraints 1×.
    """
    doc_text = (
        " ".join(entry.get("keywords", [])) + " " +
        " ".join(entry.get("keywords", [])) +
        " " + entry.get("name", "") + " " + entry.get("name", "") +
        " " + entry.get("description", "") +
        " " + " ".join(entry.get("constraints", []))
    )
    doc_tokens = _tokenize(doc_text)
    if not query_tokens or not doc_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens)
    return overlap / (len(query_tokens) + 1)


def _retrieve_local(goal: str, top_k: int, min_score: float) -> str:
    """Query the local knowledge base and return formatted context."""
    kb = _load_knowledge_base()
    query_tokens = _tokenize(goal)
    logger.debug("RAG local | query tokens: %s", query_tokens)

    scored = sorted(
        [(s, e) for e in kb if (s := _score(query_tokens, e)) >= min_score],
        key=lambda x: x[0],
        reverse=True,
    )[:top_k]

    if not scored:
        logger.info("RAG local | no matches for goal=%r", goal)
        return ""

    logger.info(
        "RAG local | matched %d protocol(s): %s",
        len(scored),
        [e["name"] for _, e in scored],
    )

    parts = []
    for _, entry in scored:
        constraints_text = "\n  - ".join(entry.get("constraints", []))
        cost_note = entry.get("cost_notes", "")
        labware = entry.get("optimal_labware", "")
        parts.append(
            f"[{entry['name']}]\n"
            f"  {entry['description']}\n"
            f"  Constraints:\n  - {constraints_text}\n"
            f"  Recommended labware: {labware}\n"
            f"  Cost note: {cost_note}"
        )
    return "\n\n".join(parts)


def retrieve_context(
    goal: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> str:
    """
    Return a formatted string of relevant protocol context to inject into
    the marketplace LLM system prompt.

    Sources (both are queried and merged):
      1. Local knowledge base  (knowledge/bio_protocols.json) — always available,
         zero latency, keyword-matched.
      2. protocols.io live API — fetches published community protocols when
         PROTOCOLS_IO_TOKEN is set; degrades gracefully if absent or offline.

    Returns an empty string if neither source finds anything relevant.
    """
    cfg = get_rag_config()
    if top_k is None:
        top_k = cfg["top_k"]
    if min_score is None:
        min_score = cfg["min_score"]

    logger.info("RAG retrieval | goal=%r | top_k=%d | min_score=%.2f", goal, top_k, min_score)

    # Source 1: local KB
    local_ctx = _retrieve_local(goal, top_k, min_score)

    # Source 2: protocols.io live fetch
    from utils.protocols_io import search_protocols
    live_ctx = search_protocols(goal, max_results=cfg.get("protocols_io_max", 2))

    sections: list[str] = []
    if local_ctx:
        sections.append("── Local protocol knowledge ──\n" + local_ctx)
    if live_ctx:
        sections.append("── Live protocols from protocols.io ──\n" + live_ctx)

    if not sections:
        logger.info("RAG retrieval | no context found from any source for goal=%r", goal)
        return ""

    combined = "\n\n".join(sections)
    logger.info(
        "RAG retrieval | combined context: %d chars (local=%d, live=%d)",
        len(combined), len(local_ctx), len(live_ctx),
    )
    return combined
