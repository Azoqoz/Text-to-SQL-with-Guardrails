from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv

from src.providers import ProviderType


class ApplicationMode(str, Enum):
    PUBLIC_DEMO = "public_demo"
    LOCAL_FULL = "local_full"


@dataclass(frozen=True)
class AppSettings:
    app_mode: ApplicationMode
    database_path: Path
    max_result_rows: int
    default_provider: ProviderType
    openai_model: str
    gemini_model: str
    anthropic_model: str
    ollama_model: str
    ollama_host: str

    @property
    def is_public_demo(self) -> bool:
        return self.app_mode == ApplicationMode.PUBLIC_DEMO

    @property
    def is_local_full(self) -> bool:
        return self.app_mode == ApplicationMode.LOCAL_FULL

    @classmethod
    def from_env(cls) -> AppSettings:
        load_dotenv()
        app_mode = _parse_app_mode(os.getenv("APP_MODE", ApplicationMode.PUBLIC_DEMO.value))
        max_result_rows = _parse_max_result_rows(os.getenv("MAX_RESULT_ROWS", "200"))
        if app_mode == ApplicationMode.PUBLIC_DEMO:
            default_provider = ProviderType.DEMO
            openai_model = ""
            gemini_model = ""
            anthropic_model = ""
            ollama_model = ""
            ollama_host = "http://localhost:11434"
        else:
            default_provider = _parse_provider(os.getenv("LLM_PROVIDER", "openai"))
            openai_model = os.getenv("OPENAI_MODEL", "")
            gemini_model = os.getenv("GEMINI_MODEL", "")
            anthropic_model = os.getenv("ANTHROPIC_MODEL", "")
            ollama_model = os.getenv("OLLAMA_MODEL", "")
            ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
            if not (ollama_host.startswith("http://") or ollama_host.startswith("https://")):
                raise ValueError("OLLAMA_HOST must start with http:// or https://")

        return cls(
            app_mode=app_mode,
            database_path=Path(os.getenv("DATABASE_PATH", "data/company.db")),
            max_result_rows=max_result_rows,
            default_provider=default_provider,
            openai_model=openai_model,
            gemini_model=gemini_model,
            anthropic_model=anthropic_model,
            ollama_model=ollama_model,
            ollama_host=ollama_host,
        )


def _parse_app_mode(value: str) -> ApplicationMode:
    try:
        return ApplicationMode(value.strip().lower())
    except ValueError as exc:
        accepted = ", ".join(mode.value for mode in ApplicationMode)
        raise ValueError(f"Unsupported APP_MODE: {value}. Expected one of: {accepted}") from exc


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
