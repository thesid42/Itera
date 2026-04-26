"""
OpenAI client for Itera.

Wraps the OpenAI API with:
  - Streaming support that calls an on_token callback
  - Model sourced from config.yaml

# ── OpenRouter (commented out) ───────────────────────────────────────────────
# Previously used OpenRouter with Qwen3. To switch back:
#   1. Set OPENROUTER_API_KEY in .env
#   2. In config.yaml set model: qwen/qwen3.6-plus and base_url: https://openrouter.ai/api/v1
#   3. Restore _get_client() to use OPENROUTER_API_KEY + base_url from config
#   4. Restore extra_body={"include_reasoning": False} in stream_response
# ─────────────────────────────────────────────────────────────────────────────
"""
import logging
import os
import re
from typing import Optional
from openai import OpenAI
from utils.config_loader import get_llm_config

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        # ── OpenAI ──────────────────────────────────────────────────────────
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise EnvironmentError(
                "OPENAI_API_KEY is not set. "
                "Export it with: export OPENAI_API_KEY=your_key_here"
            )
        cfg = get_llm_config()
        logger.debug("Initialising OpenAI client | model=%s", cfg["model"])
        _client = OpenAI(api_key=api_key)
        # ── OpenRouter (commented out) ───────────────────────────────────────
        # api_key = os.environ.get("OPENROUTER_API_KEY", "")
        # _client = OpenAI(base_url=cfg["base_url"], api_key=api_key)
    return _client


def stream_response(
    system_prompt: str,
    messages: list,
    max_tokens: int,
    temperature: float = 0.7,
    on_token=None,
) -> str:
    """
    Stream a chat completion from OpenRouter and return the full filtered text.

    Qwen3 models may emit <think>...</think> reasoning blocks inside the
    content stream. This function strips them from both the streaming callback
    and the final returned string.
    """
    cfg = get_llm_config()
    model = cfg["model"]
    client = _get_client()

    role = messages[0].get("role", "?") if messages else "?"
    preview = str(messages[0].get("content", ""))[:80] if messages else ""
    logger.info(
        "LLM request | model=%s | max_tokens=%d | temperature=%.2f | first_msg_role=%s | preview=%r",
        model, max_tokens, temperature, role, preview,
    )

    full_text = ""
    in_think = False
    chunk_count = 0

    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system_prompt}, *messages],
        max_tokens=max_tokens,
        temperature=temperature,
        stream=True,
        # extra_body={"include_reasoning": False},  # OpenRouter/Qwen3 only
    )

    for chunk in stream:
        if not chunk.choices:
            continue
        text = chunk.choices[0].delta.content or ""
        if not text:
            continue

        chunk_count += 1
        full_text += text

        if on_token:
            _emit_filtered(text, on_token, in_think_ref=[in_think])
            in_think = _in_think_state[0]

    # Strip any think blocks that leaked into full_text
    clean = re.sub(r"<think>.*?</think>", "", full_text, flags=re.DOTALL).strip()

    logger.info(
        "LLM response | model=%s | chunks=%d | raw_chars=%d | clean_chars=%d",
        model, chunk_count, len(full_text), len(clean),
    )
    logger.debug("LLM response preview: %r", clean[:200])

    return clean


# Mutable state shared between stream_response and _emit_filtered
_in_think_state = [False]


def _emit_filtered(text: str, on_token, in_think_ref: list) -> None:
    """
    Emit non-thinking portions of a streaming chunk to on_token.
    Uses in_think_ref[0] as mutable state across calls.
    """
    while text:
        if in_think_ref[0]:
            end = text.find("</think>")
            if end != -1:
                in_think_ref[0] = False
                text = text[end + len("</think>"):]
                logger.debug("Exited <think> block in stream")
            else:
                break
        else:
            start = text.find("<think>")
            if start != -1:
                visible = text[:start]
                if visible:
                    on_token(visible)
                in_think_ref[0] = True
                logger.debug("Entered <think> block in stream — suppressing from TUI")
                text = text[start + len("<think>"):]
            else:
                on_token(text)
                break

    _in_think_state[0] = in_think_ref[0]
