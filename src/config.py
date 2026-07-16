from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src.providers import ProviderType


@dataclass(frozen=True)
class AppSettings:
    database_path: Path
    max_result_rows: int
    default_provider: ProviderType
    openai_model: str
    gemini_model: str
    anthropic_model: str
    ollama_model: str
    ollama_host: str

    @classmethod
    def from_env(cls) -> AppSettings:
        load_dotenv()
        max_result_rows = _parse_max_result_rows(os.getenv("MAX_RESULT_ROWS", "200"))
        default_provider = _parse_provider(os.getenv("LLM_PROVIDER", "openai"))
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        if not (ollama_host.startswith("http://") or ollama_host.startswith("https://")):
            raise ValueError("OLLAMA_HOST must start with http:// or https://")

        return cls(
            database_path=Path(os.getenv("DATABASE_PATH", "data/company.db")),
            max_result_rows=max_result_rows,
            default_provider=default_provider,
            openai_model=os.getenv("OPENAI_MODEL", ""),
            gemini_model=os.getenv("GEMINI_MODEL", ""),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", ""),
            ollama_model=os.getenv("OLLAMA_MODEL", ""),
            ollama_host=ollama_host,
        )


def _parse_max_result_rows(value: str) -> int:
    try:
        max_result_rows = int(value)
    except ValueError as exc:
        raise ValueError("MAX_RESULT_ROWS must be an integer") from exc

    if not 1 <= max_result_rows <= 10_000:
        raise ValueError("MAX_RESULT_ROWS must be between 1 and 10,000")
    return max_result_rows


def _parse_provider(value: str) -> ProviderType:
    try:
        return ProviderType(value.strip().lower())
    except ValueError as exc:
        raise ValueError(f"Unsupported LLM_PROVIDER: {value}") from exc
