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


class AnthropicTextToSQLProvider(TextToSQLProvider):
    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str,
        model_name: str,
        max_tokens: int = 1200,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise LLMConfigurationError("Anthropic API key is required")
        if not model_name.strip():
            raise LLMConfigurationError("Anthropic model name is required")
        if max_tokens < 1:
            raise LLMConfigurationError("max_tokens must be positive")

        self.model_name = model_name
        self.max_tokens = max_tokens
        self._client = client if client is not None else self._create_client(api_key)

    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        self._validate_inputs(question, schema)
        prompt = build_text_to_sql_prompt(question, schema)
        try:
            response = self._client.messages.create(
                model=self.model_name,
                max_tokens=self.max_tokens,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = _combine_text_blocks(response)
            return parse_generated_sql_response(text, self.provider_name, self.model_name)
        except LLMResponseError:
            raise
        except Exception as exc:
            raise LLMProviderError("Anthropic provider failed to generate SQL") from exc

    def _create_client(self, api_key: str) -> Any:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMConfigurationError("Anthropic SDK is not installed") from exc
        return anthropic.Anthropic(api_key=api_key)


def _combine_text_blocks(response: Any) -> str:
    chunks: list[str] = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chunks.append(text)
            continue
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            chunks.append(block["text"])

    if not chunks:
        raise LLMResponseError("Anthropic response did not include text content")
    return "".join(chunks)
