from ..config import Settings
from .base import LLMClient, ProviderError, ProviderUnavailable
from .mistral import MistralClient
from .mock import MockLLM


def build_llm(settings: Settings) -> LLMClient:
    if settings.resolved_provider == "mistral":
        return MistralClient(settings.mistral_api_key, settings.mistral_base_url,
                             settings.mistral_model, settings.llm_timeout_s)
    return MockLLM()


__all__ = ["build_llm", "LLMClient", "ProviderError", "ProviderUnavailable", "MistralClient", "MockLLM"]
