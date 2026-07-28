from __future__ import annotations

from enum import Enum

from src.providers.anthropic_provider import AnthropicTextToSQLProvider
from src.providers.base import TextToSQLProvider
from src.providers.demo_provider import DemoTextToSQLProvider
from src.providers.gemini_provider import GeminiTextToSQLProvider
from src.providers.models import LLMConfigurationError
from src.providers.ollama_provider import OllamaTextToSQLProvider
from src.providers.openai_provider import OpenAITextToSQLProvider


class ProviderType(str, Enum):
    DEMO = "demo"
    OPENAI = "openai"
    GEMINI = "gemini"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"


def create_provider(
    provider_type: ProviderType | str,
    model_name: str,
    api_key: str | None = None,
    ollama_host: str = "http://localhost:11434",
) -> TextToSQLProvider:
    provider = _normalize_provider_type(provider_type)

    if provider == ProviderType.DEMO:
        return DemoTextToSQLProvider()
    if provider == ProviderType.OPENAI:
        return OpenAITextToSQLProvider(api_key=_require_api_key(api_key, provider), model_name=model_name)
    if provider == ProviderType.GEMINI:
        return GeminiTextToSQLProvider(api_key=_require_api_key(api_key, provider), model_name=model_name)
    if provider == ProviderType.ANTHROPIC:
        return AnthropicTextToSQLProvider(api_key=_require_api_key(api_key, provider), model_name=model_name)
    if provider == ProviderType.OLLAMA:
        return OllamaTextToSQLProvider(model_name=model_name, host=ollama_host)

    raise LLMConfigurationError("Unsupported provider")


def _normalize_provider_type(provider_type: ProviderType | str) -> ProviderType:
    if isinstance(provider_type, ProviderType):
        return provider_type
    try:
        return ProviderType(provider_type.strip().lower())
    except ValueError as exc:
        raise LLMConfigurationError(f"Unsupported provider: {provider_type}") from exc


def _require_api_key(api_key: str | None, provider: ProviderType) -> str:
    if api_key is None or not api_key.strip():
        raise LLMConfigurationError(f"API key is required for provider: {provider.value}")
    return api_key
