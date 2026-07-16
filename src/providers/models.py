from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class LLMProviderError(RuntimeError):
    pass


class LLMConfigurationError(LLMProviderError):
    pass


class LLMResponseError(LLMProviderError):
    pass


@dataclass(frozen=True)
class GeneratedSQL:
    sql: str
    explanation: str
    provider: str
    model: str

    def __post_init__(self) -> None:
        if not self.sql.strip():
            raise ValueError("Generated SQL must not be empty")
        if not self.provider.strip():
            raise ValueError("Provider must not be empty")
        if not self.model.strip():
            raise ValueError("Model must not be empty")
        if not self.explanation.strip():
            object.__setattr__(self, "explanation", "SQL generated from the supplied schema and question.")


def parse_generated_sql_response(text: str, provider: str, model: str) -> GeneratedSQL:
    payload = _strip_json_fence(text.strip())
    try:
        decoded: Any = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMResponseError("Provider returned invalid JSON") from exc

    if not isinstance(decoded, dict):
        raise LLMResponseError("Provider JSON response must be an object")

    sql = decoded.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise LLMResponseError("Provider JSON response is missing SQL")

    explanation = decoded.get("explanation", "")
    if not isinstance(explanation, str):
        explanation = ""

    try:
        return GeneratedSQL(
            sql=sql.strip(),
            explanation=explanation.strip(),
            provider=provider,
            model=model,
        )
    except ValueError as exc:
        raise LLMResponseError("Provider JSON response could not be converted to GeneratedSQL") from exc


def _strip_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()

    return text
