"""
Supermemory integration — stores and retrieves (error, diff, fix) triples
so the compiler can learn from past simulation failures.

Requires SUPERMEMORY_API_KEY in .env. If the key is absent the functions
are silent no-ops, so the rest of the app works without it.
"""
import os
import logging

logger = logging.getLogger(__name__)


def _client():
    api_key = os.getenv("SUPERMEMORY_API_KEY")
    if not api_key:
        return None
    try:
        from supermemory import Supermemory
        return Supermemory(api_key=api_key)
    except ImportError:
        logger.warning("Supermemory | package not installed — run: pip install --pre supermemory")
        return None
    except Exception as exc:
        logger.warning("Supermemory | client init failed: %s", exc)
        return None


def store_fix(error: str, diff: str, fixed_code: str) -> bool:
    """
    Persist a successful error→fix triple after a simulation error is resolved.
    Called by the iteration engine when attempt N+1 passes after attempt N failed.
    """
    client = _client()
    if not client:
        return False
    try:
        content = (
            "OPENTRONS SIMULATION ERROR:\n"
            f"{error.strip()}\n\n"
            "CODE DIFF (what was changed to fix it):\n"
            f"{diff.strip()}\n\n"
            "FIXED CODE:\n"
            f"{fixed_code.strip()}"
        )
        client.documents.add(content=content)
        logger.info("Supermemory | stored fix | error_prefix=%r", error[:80])
        return True
    except Exception as exc:
        logger.warning("Supermemory | store_fix failed: %s", exc)
        return False


def retrieve_fixes(error: str, limit: int = 3) -> list:
    """
    Search Supermemory for past fixes semantically similar to the current error.
    Returns a list of content strings (each containing error + diff + fixed code).
    Returns [] on any failure so callers never need to handle exceptions.
    """
    client = _client()
    if not client:
        return []
    try:
        response = client.search.execute(q=error[:500])
        results = getattr(response, "results", []) or []
        fixes = []
        for r in results[:limit]:
            content = (
                getattr(r, "content", None)
                or getattr(getattr(r, "document", None), "content", None)
                or ""
            )
            if content:
                fixes.append(content)
        logger.info("Supermemory | retrieved %d fix(es) for error_prefix=%r", len(fixes), error[:80])
        return fixes
    except Exception as exc:
        logger.warning("Supermemory | retrieve_fixes failed: %s", exc)
        return []
