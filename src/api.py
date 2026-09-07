"""Synchronous HTTP adapter. No SQL validation or authorization lives here."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt

from src.application import ApplicationError, Capabilities, ProviderOptions, TextToSQLApplication
from src.config import AppSettings
from src.models import TextToSQLResponse
from src.providers.factory import ProviderType
from src.rbac import UserContext, UserRole


class QueryBody(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    question: str
    user_id: str
    role: UserRole
    employee_id: StrictInt | None = None
    provider: ProviderType | None = None
    model_name: str | None = None
    api_key: SecretStr | None = Field(default=None, exclude=True)
    ollama_host: str | None = None


def create_app(
    settings: AppSettings | None = None,
    *,
    service: TextToSQLApplication | None = None,
) -> FastAPI:
    """Use with `uvicorn src.api:create_app --factory` or inject a service in tests."""
    if service is None:
        try:
            service = TextToSQLApplication(settings or AppSettings.from_env())
        except Exception:
            raise ApplicationError("configuration_error", "Application configuration is invalid.") from None

    api = FastAPI(title="Text-to-SQL", docs_url=None, redoc_url=None, openapi_url=None, debug=False)

    @api.exception_handler(RequestValidationError)
    def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        # Default Pydantic/FastAPI errors can contain the submitted body/key.
        return JSONResponse(status_code=422, content={
            "error_type": "validation_error", "error_message": "The request is invalid.",
        })

    @api.exception_handler(ValueError)
    def invalid_context(request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=422, content={
            "error_type": "validation_error", "error_message": "The request is invalid.",
        })

    @api.exception_handler(ApplicationError)
    def unavailable(request: Request, error: ApplicationError) -> JSONResponse:
        return JSONResponse(status_code=503, content={
            "error_type": error.error_type, "error_message": error.message,
        })

    @api.middleware("http")
    async def safe_response(request: Request, call_next):
        # Catch unexpected errors before the server's traceback logger. Query
        # execution itself remains synchronous in the route's worker thread.
        try:
            response = await call_next(request)
        except Exception:
            response = JSONResponse(status_code=500, content={
                "error_type": "internal_error", "error_message": "The request could not be completed.",
            })
        response.headers["Cache-Control"] = "no-store"
        return response

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/capabilities", response_model=Capabilities)
    def capabilities(
        role: UserRole = UserRole.SALES_ANALYST,
        employee_id: int | None = None,
    ) -> Capabilities:
        return service.capabilities(UserContext("metadata", role, employee_id))

    @api.post("/query", response_model=TextToSQLResponse)
    def query(body: QueryBody) -> TextToSQLResponse:
        return service.query(
            body.question,
            UserContext(body.user_id, body.role, body.employee_id),
            ProviderOptions(
                provider=body.provider, model_name=body.model_name,
                api_key=body.api_key.get_secret_value() if body.api_key is not None else None,
                ollama_host=body.ollama_host,
            ),
        )

    return api
