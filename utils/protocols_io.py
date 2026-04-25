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

def search_protocols(query: str, max_results: int = 3) -> str:
    """
    Search protocols.io for protocols relevant to the given query string.

    Returns a formatted multi-section string to inject into the bio RAG
    context, or an empty string if the token is missing / API is down.

    Strategy:
      - Search for up to max_results protocols.
      - Fetch full detail (steps + reagents) for the top hit only.
      - Format the top hit with full detail; remainder as summaries.
    """
    token = os.environ.get("PROTOCOLS_IO_TOKEN", "")
    if not token:
        logger.info("PROTOCOLS_IO_TOKEN not set — skipping live protocol fetch")
        return ""

    logger.info("protocols.io | searching for query=%r (max=%d)", query, max_results)
    items = _search(query, page_size=max_results + 2)  # fetch a few extra; filter below
    if not items:
        return ""

    items = items[:max_results]
    parts: list[str] = []

    for i, item in enumerate(items):
        if i == 0 and item.get("id"):
            # Fetch full detail for the best match
            logger.debug("protocols.io | fetching full detail for top result id=%r", item["id"])
            detail = _fetch_detail(item["id"])
            if detail:
                # The detail endpoint may wrap the payload
                payload = detail.get("payload", detail)
                parts.append(_format_summary(payload, include_steps=True))
            else:
                parts.append(_format_summary(item, include_steps=False))
        else:
            parts.append(_format_summary(item, include_steps=False))

    result = "\n\n".join(parts)
    logger.info(
        "protocols.io | formatted %d protocol(s) | %d chars of context",
        len(parts), len(result),
    )
    return result
