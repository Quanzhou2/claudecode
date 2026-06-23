"""Factory for an OpenAI-compatible chat client.

Works with any provider exposing the OpenAI Chat Completions API:
OpenAI, Azure OpenAI, DeepSeek, Qwen/DashScope, Moonshot, Zhipu, Ollama,
vLLM, LM Studio, etc. The provider is selected purely via configuration
(``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL`` / ``LLM_VISION_MODEL``).
"""
from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from ..config import get_settings


@lru_cache
def get_client() -> OpenAI | None:
    """Return a configured client, or None when no API key is set."""
    settings = get_settings()
    if not settings.llm_enabled:
        return None
    return OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
