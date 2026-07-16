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


class OpenAITextToSQLProvider(TextToSQLProvider):
    provider_name = "openai"

    def __init__(self, api_key: str, model_name: str, client: Any | None = None) -> None:
        if not api_key.strip():
            raise LLMConfigurationError("OpenAI API key is required")
        if not model_name.strip():
            raise LLMConfigurationError("OpenAI model name is required")

        self.model_name = model_name
        self._client = client if client is not None else self._create_client(api_key)

    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        self._validate_inputs(question, schema)
        prompt = build_text_to_sql_prompt(question, schema)
        try:
            response = self._client.responses.create(model=self.model_name, input=prompt)
            text = _extract_output_text(response)
            return parse_generated_sql_response(text, self.provider_name, self.model_name)
        except LLMResponseError:
            raise
        except Exception as exc:
            raise LLMProviderError("OpenAI provider failed to generate SQL") from exc

    def _create_client(self, api_key: str) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMConfigurationError("OpenAI SDK is not installed") from exc
        return OpenAI(api_key=api_key)


def _extract_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                chunks.append(text)
    if chunks:
        return "".join(chunks)

    raise LLMResponseError("OpenAI response did not include output text")
