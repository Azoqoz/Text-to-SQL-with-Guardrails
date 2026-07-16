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


class GeminiTextToSQLProvider(TextToSQLProvider):
    provider_name = "gemini"

    def __init__(self, api_key: str, model_name: str, client: Any | None = None) -> None:
        if not api_key.strip():
            raise LLMConfigurationError("Gemini API key is required")
        if not model_name.strip():
            raise LLMConfigurationError("Gemini model name is required")

        self.model_name = model_name
        self._client = client if client is not None else self._create_client(api_key)

    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        self._validate_inputs(question, schema)
        prompt = build_text_to_sql_prompt(question, schema)
        try:
            response = self._client.models.generate_content(model=self.model_name, contents=prompt)
            text = getattr(response, "text", None)
            if not isinstance(text, str) or not text.strip():
                raise LLMResponseError("Gemini response did not include text")
            return parse_generated_sql_response(text, self.provider_name, self.model_name)
        except LLMResponseError:
            raise
        except Exception as exc:
            raise LLMProviderError("Gemini provider failed to generate SQL") from exc

    def _create_client(self, api_key: str) -> Any:
        try:
            from google import genai
        except ImportError as exc:
            raise LLMConfigurationError("google-genai SDK is not installed") from exc
        return genai.Client(api_key=api_key)
