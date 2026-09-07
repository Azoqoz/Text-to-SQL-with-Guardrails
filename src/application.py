"""Framework-neutral entry point for the next frontend.

SQL authorization and execution remain exclusively in TextToSQLService.
The example catalog mirrors app.EXAMPLE_QUESTIONS until Streamlit is migrated;
tests enforce that parity without importing Streamlit into the backend.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace
from typing import Callable

from src.config import AppSettings, ApplicationMode
from src.database import SQLiteReadOnlyDatabase
from src.models import TextToSQLRequest, TextToSQLResponse
from src.providers.base import TextToSQLProvider
from src.providers.demo_provider import DemoTextToSQLProvider
from src.providers.factory import ProviderType, create_provider
from src.providers.models import LLMConfigurationError, LLMProviderError
from src.rbac import UserContext, UserRole
from src.service import TextToSQLService, map_service_error


EXAMPLE_QUESTIONS = {
    UserRole.SALES_ANALYST: (
        "What are the top 5 products by total completed-order revenue?",
        "Show monthly completed-order revenue for 2025.",
        "Which regions have the highest number of completed orders?",
        "Which customers placed the most completed orders?",
    ),
    UserRole.SALES_MANAGER: (
        "Which employees manage the highest-revenue customer portfolios?",
        "Show customer count by account manager.",
        "Compare completed-order revenue by region.",
        "Which products generate the most revenue?",
    ),
    UserRole.ACCOUNT_MANAGER: (
        "Show my assigned customers.",
        "Which of my customers generated the most revenue?",
        "Show completed orders for my customers.",
        "What products were purchased most by my customers?",
    ),
}
LOCAL_PROVIDERS = (
    ProviderType.OPENAI, ProviderType.GEMINI, ProviderType.ANTHROPIC, ProviderType.OLLAMA,
)


@dataclass(frozen=True)
class ProviderOptions:
    provider: ProviderType | None = None
    model_name: str | None = None
    api_key: str | None = field(default=None, repr=False)
    ollama_host: str | None = None


@dataclass(frozen=True)
class ProviderCapability:
    provider: ProviderType
    model_name: str
    requires_api_key: bool
    supports_host: bool


@dataclass(frozen=True)
class RoleCapability:
    role: UserRole
    requires_employee_id: bool
    example_questions: tuple[str, ...]


@dataclass(frozen=True)
class Capabilities:
    app_mode: ApplicationMode
    max_result_rows: int
    default_provider: ProviderType
    allows_free_text: bool
    providers: list[ProviderCapability]
    roles: list[RoleCapability]
    role: UserRole
    schema: str
    ollama_host: str | None


class ApplicationError(Exception):
    """Safe error for metadata/configuration failures outside a query response."""

    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message


class TextToSQLApplication:
    def __init__(
        self,
        settings: AppSettings,
        *,
        provider_factory: Callable[..., TextToSQLProvider] = create_provider,
        database_factory: Callable[..., SQLiteReadOnlyDatabase] = SQLiteReadOnlyDatabase,
    ) -> None:
        self.settings = settings
        self._provider_factory = provider_factory
        self._database_factory = database_factory

    def _database(self) -> SQLiteReadOnlyDatabase:
        return self._database_factory(
            database_path=self.settings.database_path,
            max_result_rows=self.settings.max_result_rows,
        )

    def _default_model(self, provider: ProviderType) -> str:
        if provider == ProviderType.DEMO:
            return DemoTextToSQLProvider.model_name
        return getattr(self.settings, f"{provider.value}_model")

    def capabilities(self, user: UserContext) -> Capabilities:
        try:
            schema = self._database().get_schema_for_user(user)
        except (OSError, sqlite3.Error):
            raise ApplicationError("database_error", "The database schema is unavailable.") from None
        demo = self.settings.is_public_demo
        providers = (ProviderType.DEMO,) if demo else LOCAL_PROVIDERS
        if not demo and self.settings.default_provider == ProviderType.DEMO:
            providers = (ProviderType.DEMO, *providers)
        return Capabilities(
            app_mode=self.settings.app_mode,
            max_result_rows=self.settings.max_result_rows,
            default_provider=ProviderType.DEMO if demo else self.settings.default_provider,
            allows_free_text=not demo,
            providers=[
                ProviderCapability(
                    provider=provider,
                    model_name=self._default_model(provider),
                    requires_api_key=provider not in {ProviderType.DEMO, ProviderType.OLLAMA},
                    supports_host=provider == ProviderType.OLLAMA,
                )
                for provider in providers
            ],
            roles=[
                RoleCapability(role, role == UserRole.ACCOUNT_MANAGER, EXAMPLE_QUESTIONS[role])
                for role in UserRole
            ],
            role=user.role,
            schema=schema,
            ollama_host=None if demo else self.settings.ollama_host,
        )

    def query(
        self,
        question: str,
        user: UserContext,
        options: ProviderOptions | None = None,
    ) -> TextToSQLResponse:
        options = options or ProviderOptions()
        provider_type = ProviderType.DEMO if self.settings.is_public_demo else (
            options.provider or self.settings.default_provider
        )
        model_name = ""
        try:
            request = TextToSQLRequest(question, user)
            if self.settings.is_public_demo:
                if question not in EXAMPLE_QUESTIONS[user.role]:
                    raise ValueError("Question is not in the role's demo catalog")
                provider = DemoTextToSQLProvider()
                model_name = provider.model_name
            else:
                provider_type = ProviderType(provider_type)
                model_name = options.model_name if options.model_name is not None else self._default_model(provider_type)
                try:
                    provider = self._provider_factory(
                        provider_type=provider_type,
                        model_name=model_name,
                        api_key=options.api_key or None,
                        ollama_host=options.ollama_host if options.ollama_host is not None else self.settings.ollama_host,
                    )
                except LLMProviderError:
                    raise
                except Exception:
                    raise LLMConfigurationError("Selected provider could not be initialized.") from None
            response = TextToSQLService(self._database(), provider).answer(request)
        except Exception as error:
            if isinstance(error, OSError):
                error_type, message = "database_error", "The database is unavailable."
            else:
                error_type, message = map_service_error(error)
            response = TextToSQLResponse(
                success=False, question=question, provider=str(getattr(provider_type, "value", provider_type)),
                model=model_name, role=user.role, generated_sql="", authorized_sql="", explanation="",
                error_type=error_type, error_message=message,
            )
        # Providers normally never see the key in their prompt. Still prevent a
        # misbehaving SDK/provider from reflecting the credential in any output.
        return _redact_credential(response, options.api_key)


def _redact_credential(response: TextToSQLResponse, api_key: str | None) -> TextToSQLResponse:
    if not api_key:
        return response

    def scrub(value):
        if isinstance(value, str):
            return value.replace(api_key, "[REDACTED]")
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {scrub(key): scrub(item) for key, item in value.items()}
        return value

    return replace(response, **{
        name: scrub(getattr(response, name))
        for name in (
            "question", "provider", "model", "generated_sql", "authorized_sql", "explanation",
            "rows", "guardrail_checks", "rbac_checks", "error_type", "error_message",
        )
    })
