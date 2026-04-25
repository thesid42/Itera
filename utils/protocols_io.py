"""
protocols.io API client.

Fetches real published lab protocols to augment the bio RAG context before
the marketplace LLM call.  Requires PROTOCOLS_IO_TOKEN in the environment
(set it in .env).

API reference: https://www.protocols.io/developers
Base URL:      https://www.protocols.io/api/v4

Flow
----
1. search_protocols(query) — full pipeline entry point used by bio_rag.py
   a. _search(query, n)   — GET /protocols?query=... → list of summaries
   b. _fetch_detail(doi)  — GET /protocols/{doi}    → steps + reagents for
                            the top hit (one extra call, much richer context)
2. Returns a formatted string ready to inject into the LLM system prompt.
   Returns "" silently if the token is absent or the API is unreachable so
   the rest of the RAG pipeline degrades gracefully.
"""
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
load_dotenv()  # load PROTOCOLS_IO_TOKEN from .env if present

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://www.protocols.io/api/v3"
_TIMEOUT = 12  # seconds per request


# ── Internal helpers ──────────────────────────────────────────────────────────

def _headers() -> dict:
    token = os.environ.get("PROTOCOLS_IO_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", clean).strip()


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"\b[a-z]{2,}\b", (text or "").lower()))


_STOPWORDS: set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "from", "has",
    "have", "how", "i", "in", "into", "is", "it", "min", "minutes", "ml", "of",
    "on", "or", "plate", "run", "runtime", "then", "the", "to", "ul", "well",
    "with", "x",
}


def _build_search_queries(goal: str) -> list[str]:
    """
    protocols.io search is closer to keyword search than full NL search.
    Build a small cascade of shorter queries.
    """
    goal = (goal or "").strip()
    if not goal:
        return []

    # 1) Original (sometimes works)
    candidates: list[str] = [goal]

    # 2) First clause / sentence-ish
    first = re.split(r"[.;\n]", goal, maxsplit=1)[0].strip()
    if first and first not in candidates:
        candidates.append(first)

    # 3) Keyword-compressed query (drop units + stopwords, keep order)
    tokens_in_order = re.findall(r"\b[a-z]{2,}\b", goal.lower())
    keywords: list[str] = []
    for t in tokens_in_order:
        if t in _STOPWORDS:
            continue
        if t.isdigit():
            continue
        if t not in keywords:
            keywords.append(t)
    if keywords:
        candidates.append(" ".join(keywords[:8]))
        candidates.append(" ".join(keywords[:5]))

    # De-dupe + cap length (avoid sending huge strings)
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        c2 = re.sub(r"\s+", " ", c).strip()
        if not c2 or c2 in seen:
            continue
        seen.add(c2)
        out.append(c2[:120])
    return out[:6]


def _score_query_against_item(query: str, item: dict) -> float:
    """
    Lightweight ranking to compensate for API ordering quirks.
    Higher is better. Pure lexical overlap (no ML deps).
    """
    q = _tokenize(query)
    if not q:
        return 0.0

    title = _strip_html(item.get("title", ""))
    desc = _strip_html(item.get("description", "") or item.get("plain_description", ""))
    kw = item.get("keywords", [])
    if isinstance(kw, list):
        kw_text = " ".join(k if isinstance(k, str) else (k.get("name", "") or "") for k in kw)
    else:
        kw_text = ""

    # Weight title + keywords higher than description
    doc = f"{title} {title} {kw_text} {kw_text} {desc}"
    d = _tokenize(doc)
    overlap = len(q & d)
    return overlap / (len(q) + 1)


@dataclass(frozen=True)
class RetrievedProtocol:
    id: int
    title: str
    doi: str
    uri: str
    score: float
    summary: str
    steps: list[str]
    materials: list[str]


def _extract_steps(detail: dict, max_steps: int = 10) -> list[str]:
    steps = detail.get("steps", []) or []
    extracted: list[str] = []
    for step in steps:
        raw = step.get("description", "") or ""
        for comp in step.get("components", []) or []:
            raw += " " + (comp.get("description", "") or "")
        text = _strip_html(raw)
        if text:
            extracted.append(text[:350])
        if len(extracted) >= max_steps:
            break
    return extracted


def _extract_materials(detail: dict, max_items: int = 12) -> list[str]:
    reagents = detail.get("reagents", []) or detail.get("materials", []) or []
    names: list[str] = []
    for r in reagents:
        name = (r.get("name", "") or r.get("title", "") or "").strip()
        if name:
            names.append(_strip_html(name)[:120])
        if len(names) >= max_items:
            break
    return names


def _search(query: str, page_size: int = 5) -> list[dict]:
    """
    GET /protocols — returns a list of protocol summary dicts.
    Fields used: title, description, doi, uri, keywords.

    protocols.io requires `filter` (string) for this endpoint; omitting it
    yields HTTP 400 "filter is required".

    For the current public REST API, the search term param is `key`.
    """
    params = {
        "filter": "public",
        "key": query,
        "page_size": page_size,
        "page_id": 1,
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{_BASE}/protocols", headers=_headers(), params=params)
        if not resp.is_success:
            body = resp.text[:400]
            # protocols.io returns 400 with status_code 1218 for bad/expired tokens
            if "1218" in body:
                logger.warning(
                    "protocols.io auth failed — PROTOCOLS_IO_TOKEN is invalid or expired. "
                    "Get a fresh token at https://www.protocols.io/developers"
                )
            else:
                logger.warning(
                    "protocols.io search HTTP %d for query=%r | body: %s",
                    resp.status_code, query, body,
                )
            return []
        data = resp.json()
        items = data.get("items", [])
        logger.info(
            "protocols.io search | query=%r | returned %d result(s)", query, len(items)
        )
        return items
    except httpx.TimeoutException:
        logger.warning("protocols.io search timed out after %ds for query=%r", _TIMEOUT, query)
        return []
    except Exception as exc:
        logger.warning("protocols.io search failed for query=%r: %s", query, exc)
        return []


def _fetch_detail(protocol_id: str | int) -> Optional[dict]:
    """
    GET /protocols/{id} — returns the full protocol object including steps
    and reagents. Called only for the single top search result.
    """
    if not protocol_id:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.get(f"{_BASE}/protocols/{protocol_id}", headers=_headers())
        resp.raise_for_status()
        detail = resp.json()
        logger.debug("protocols.io detail fetched for id=%r", protocol_id)
        return detail
    except Exception as exc:
        logger.warning("protocols.io detail fetch failed for id=%r: %s", protocol_id, exc)
        return None


def _format_summary(item: dict, include_steps: bool = False) -> str:
    """
    Convert a protocols.io API item dict into a concise context string
    suitable for LLM injection.
    """
    title = _strip_html(item.get("title", "Untitled protocol"))
    description = _strip_html(item.get("description", "") or item.get("plain_description", ""))
    doi = item.get("doi", "")
    keywords = item.get("keywords", [])
    if isinstance(keywords, list):
        kw_str = ", ".join(k if isinstance(k, str) else k.get("name", "") for k in keywords[:8])
    else:
        kw_str = ""

    lines = [f"[protocols.io — {title}]"]
    if doi:
        lines.append(f"  DOI: {doi}")
    if description:
        lines.append(f"  Summary: {description[:400]}")
    if kw_str:
        lines.append(f"  Keywords: {kw_str}")

    if include_steps:
        steps = item.get("steps", [])
        extracted = []
        for step in steps[:8]:
            # Steps may be nested under "components" in v4
            raw = step.get("description", "") or ""
            # Some protocols nest component descriptions
            for comp in step.get("components", []):
                raw += " " + (comp.get("description", "") or "")
            text = _strip_html(raw)[:250]
            if text:
                extracted.append(text)
        if extracted:
            lines.append("  Key steps:")
            for s in extracted:
                lines.append(f"    - {s}")

        reagents = item.get("reagents", []) or item.get("materials", [])
        if reagents:
            names = [
                r.get("name", "") or r.get("title", "")
                for r in reagents[:10]
                if r.get("name") or r.get("title")
            ]
            if names:
                lines.append(f"  Reagents/Materials: {', '.join(names)}")

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def retrieve_protocols(
    query: str,
    *,
    max_results: int = 3,
    detail_results: int = 2,
) -> list[RetrievedProtocol]:
    """
    Retrieve and rank protocols for a query, returning structured results.
    """
    token = os.environ.get("PROTOCOLS_IO_TOKEN", "")
    if not token:
        logger.info("PROTOCOLS_IO_TOKEN not set — skipping live protocol fetch")
        return []

    # Pull extra results then apply our own scoring + de-dupe.
    # Use a cascade of queries because the API behaves like keyword search.
    items: list[dict] = []
    used_query = query
    for q in _build_search_queries(query):
        used_query = q
        items = _search(q, page_size=max(10, max_results * 6))
        if items:
            break
    if not items:
        logger.info("protocols.io | no results for any query variant | original=%r", query)
        return []

    # De-dupe by id
    by_id: dict[int, dict] = {}
    for it in items:
        pid = it.get("id")
        if isinstance(pid, int) and pid not in by_id:
            by_id[pid] = it

    ranked = sorted(
        by_id.values(),
        key=lambda it: _score_query_against_item(used_query, it),
        reverse=True,
    )[:max_results]

    results: list[RetrievedProtocol] = []
    for idx, it in enumerate(ranked):
        pid = it.get("id")
        if not isinstance(pid, int):
            continue

        score = _score_query_against_item(used_query, it)
        title = _strip_html(it.get("title", "Untitled protocol"))
        doi = str(it.get("doi", "") or "")
        uri = str(it.get("uri", "") or it.get("url", "") or "")
        summary = _strip_html(it.get("description", "") or it.get("plain_description", ""))[:500]

        steps: list[str] = []
        materials: list[str] = []

        if idx < max(0, detail_results):
            detail = _fetch_detail(pid)
            if detail:
                payload = detail.get("payload", detail)
                steps = _extract_steps(payload)
                materials = _extract_materials(payload)

        results.append(
            RetrievedProtocol(
                id=pid,
                title=title,
                doi=doi,
                uri=uri,
                score=score,
                summary=summary,
                steps=steps,
                materials=materials,
            )
        )

    return results


def format_protocols_for_rag(protocols: list[RetrievedProtocol]) -> str:
    """
    Format retrieved protocols into a compact context block for LLM injection.
    """
    parts: list[str] = []
    for p in protocols:
        lines: list[str] = [f"[protocols.io — {p.title}] (score={p.score:.2f})"]
        if p.doi:
            lines.append(f"  DOI: {p.doi}")
        if p.uri:
            lines.append(f"  URL: {p.uri}")
        if p.summary:
            lines.append(f"  Summary: {p.summary}")
        if p.steps:
            lines.append("  Key steps:")
            for s in p.steps[:8]:
                lines.append(f"    - {s}")
        if p.materials:
            lines.append(f"  Materials: {', '.join(p.materials[:10])}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts).strip()


def search_protocols(query: str, max_results: int = 3) -> str:
    """
    Search protocols.io for protocols relevant to the given query string.

    Returns a formatted multi-section string to inject into the bio RAG
    context, or an empty string if the token is missing / API is down.

    Strategy:
      - Retrieve and re-rank protocols.
      - Fetch full detail (steps + materials) for the top few results.
      - Format to a compact context string for LLM injection.
    """
    logger.info("protocols.io | retrieving for query=%r (max=%d)", query, max_results)
    prots = retrieve_protocols(query, max_results=max_results, detail_results=min(2, max_results))
    if not prots:
        return ""
    formatted = format_protocols_for_rag(prots)
    logger.info("protocols.io | formatted %d protocol(s) | %d chars", len(prots), len(formatted))
    return formatted
