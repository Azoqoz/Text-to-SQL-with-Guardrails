from __future__ import annotations

import sqlite3

from src.database import SQLiteReadOnlyDatabase
from src.guardrails import UnsafeSQLError
from src.models import TextToSQLRequest, TextToSQLResponse
from src.providers.base import TextToSQLProvider
from src.providers.models import LLMConfigurationError, LLMProviderError, LLMResponseError
from src.rbac import AccessDeniedError, RBACEnforcer, UserRole


class TextToSQLService:
    def __init__(
        self,
        database: SQLiteReadOnlyDatabase,
        provider: TextToSQLProvider,
        rbac_enforcer: RBACEnforcer | None = None,
    ) -> None:
        self.database = database
        self.provider = provider
        self.rbac_enforcer = rbac_enforcer or RBACEnforcer()

    def answer(self, request: TextToSQLRequest) -> TextToSQLResponse:
        try:
            request = TextToSQLRequest(question=request.question, user=request.user)
            schema = self.database.get_schema_for_user(request.user)
            generated = self.provider.generate_sql(request.question, schema)
            physical_schema = self.database.get_physical_schema()
            rbac_result = self.rbac_enforcer.enforce(
                sql=generated.sql,
                user=request.user,
                physical_schema=physical_schema,
            )
            query_result = self.database.execute_authorized(rbac_result)

            return TextToSQLResponse(
                success=True,
                question=request.question,
                provider=generated.provider,
                model=generated.model,
                role=request.user.role,
                generated_sql=generated.sql,
                authorized_sql=rbac_result.sql,
                explanation=generated.explanation,
                rows=query_result.rows,
                row_count=query_result.row_count,
                truncated=query_result.truncated,
                row_level_security_applied=rbac_result.row_level_security_applied,
                guardrail_checks=rbac_result.guardrail_checks,
                rbac_checks=rbac_result.rbac_checks,
            )
        except (
            LLMConfigurationError,
            LLMResponseError,
            LLMProviderError,
            UnsafeSQLError,
            AccessDeniedError,
            sqlite3.Error,
            ValueError,
        ) as exc:
            error_type, message = map_service_error(exc)
            return TextToSQLResponse(
                success=False,
                question=getattr(request, "question", ""),
                provider=getattr(self.provider, "provider_name", ""),
                model=getattr(self.provider, "model_name", ""),
                role=getattr(getattr(request, "user", None), "role", UserRole.SALES_ANALYST),
                generated_sql="",
                authorized_sql="",
                explanation="",
                error_type=error_type,
                error_message=message,
            )


def map_service_error(error: Exception) -> tuple[str, str]:
    if isinstance(error, LLMConfigurationError):
        return "configuration_error", "LLM provider is not configured correctly."
    if isinstance(error, LLMResponseError):
        return "invalid_provider_response", "The provider returned an invalid SQL response."
    if isinstance(error, LLMProviderError):
        return "provider_error", "The provider could not generate SQL."
    if isinstance(error, UnsafeSQLError):
        return "unsafe_sql", "Generated SQL did not pass safety checks."
    if isinstance(error, AccessDeniedError):
        return "access_denied", "The query is not allowed for this user role."
    if isinstance(error, sqlite3.Error):
        return "database_error", "The database could not execute the authorized query."
    if isinstance(error, ValueError):
        return "validation_error", "The request is invalid."
    return "provider_error", "The request could not be completed."
