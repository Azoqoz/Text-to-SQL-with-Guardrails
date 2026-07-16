from __future__ import annotations

from abc import ABC, abstractmethod

from src.providers.models import GeneratedSQL, LLMConfigurationError


class TextToSQLProvider(ABC):
    provider_name: str
    model_name: str

    def _validate_inputs(self, question: str, schema: str) -> None:
        if not question.strip():
            raise LLMConfigurationError("Question must not be empty")
        if not schema.strip():
            raise LLMConfigurationError("Schema must not be empty")

    @abstractmethod
    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        """Generate SQL text only; providers must not execute SQL."""
