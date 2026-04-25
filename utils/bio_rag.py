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


def retrieve_context(
    goal: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> str:
    """
    Given a lab goal string, return a formatted string of relevant protocol
    constraints to inject into the marketplace LLM system prompt.

    Returns an empty string if no relevant protocols are found.
    """
    cfg = get_rag_config()
    if top_k is None:
        top_k = cfg["top_k"]
    if min_score is None:
        min_score = cfg["min_score"]

    logger.info("RAG retrieval | goal=%r | top_k=%d | min_score=%.2f", goal, top_k, min_score)

    kb = _load_knowledge_base()
    query_tokens = _tokenize(goal)
    logger.debug("Query tokens: %s", query_tokens)

    scored = sorted(
        [(s, e) for e in kb if (s := _score(query_tokens, e)) >= min_score],
        key=lambda x: x[0],
        reverse=True,
    )[:top_k]

    if not scored:
        logger.info("RAG retrieval | no matching protocols found for goal=%r", goal)
        return ""

    logger.info(
        "RAG retrieval | matched %d protocol(s): %s",
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
