from __future__ import annotations

from typing import Any

from src.providers.base import TextToSQLProvider
from src.providers.models import (
    GeneratedSQL,
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseError,
    parse_generated_sql_response,
)
from src.providers.prompt import build_text_to_sql_prompt


class OllamaTextToSQLProvider(TextToSQLProvider):
    provider_name = "ollama"

    def __init__(
        self,
        model_name: str,
        host: str = "http://localhost:11434",
        client: Any | None = None,
    ) -> None:
        if not model_name.strip():
            raise LLMConfigurationError("Ollama model name is required")
        if not (host.startswith("http://") or host.startswith("https://")):
            raise LLMConfigurationError("Ollama host must start with http:// or https://")

        self.model_name = model_name
        self.host = host
        self._client = client if client is not None else self._create_client(host)

    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        self._validate_inputs(question, schema)
        prompt = build_text_to_sql_prompt(question, schema)
        try:
            response = self._client.chat(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
            )
            text = _extract_message_content(response)
            return parse_generated_sql_response(text, self.provider_name, self.model_name)
        except LLMResponseError:
            raise
        except Exception as exc:
            raise LLMProviderError("Ollama provider failed. Ensure the local Ollama server is running.") from exc

    def _create_client(self, host: str) -> Any:
        try:
            import ollama
        except ImportError as exc:
            raise LLMConfigurationError("Ollama SDK is not installed") from exc
        return ollama.Client(host=host)


def _extract_message_content(response: Any) -> str:
    if isinstance(response, dict):
        message = response.get("message", {})
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content.strip():
            return content

    message = getattr(response, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content

    raise LLMResponseError("Ollama response did not include assistant content")
