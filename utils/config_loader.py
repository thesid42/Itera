"""
Centralized config loader for Itera.
Reads config.yaml once and caches it for the lifetime of the process.
"""
import yaml
import os
from functools import lru_cache

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")


@lru_cache(maxsize=1)
def get_config() -> dict:
    with open(os.path.abspath(_CONFIG_PATH)) as f:
        return yaml.safe_load(f)


def get_pricing() -> dict:
    return get_config()["pricing"]


def get_llm_config() -> dict:
    return get_config()["llm"]


def get_rag_config() -> dict:
    return get_config().get("rag", {"top_k": 3, "min_score": 0.05})
