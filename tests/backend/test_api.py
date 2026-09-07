import inspect
import json
import threading
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.api import QueryBody, create_app
from src.application import EXAMPLE_QUESTIONS, ProviderOptions, TextToSQLApplication
from src.config import ApplicationMode
from src.providers.factory import ProviderType
from src.providers.models import GeneratedSQL
from src.providers.openai_provider import OpenAITextToSQLProvider
from src.rbac import UserContext, UserRole


SECRET = "sk-http-test-never-persist-or-return"
DEMO_BODY = {
    "question": "Show my assigned customers.", "user_id": "am1", "role": "account_manager", "employee_id": 1,
}
LOCAL_BODY = {"question": "List customers", "user_id": "analyst", "role": "sales_analyst", "api_key": SECRET}


def local_application(settings, provider, database):
    return TextToSQLApplication(
        replace(settings, app_mode=ApplicationMode.LOCAL_FULL, default_provider=ProviderType.OPENAI),
        provider_factory=lambda **kwargs: provider, database_factory=lambda **kwargs: database,
    )


def test_only_three_routes_and_health_does_not_touch_database(settings, tmp_path):
    missing = tmp_path / "missing.db"
    api = create_app(replace(settings, database_path=missing))
    assert {(route.path, tuple(sorted(route.methods))) for route in api.routes} == {
        ("/health", ("GET",)), ("/capabilities", ("GET",)), ("/query", ("POST",)),
    }
    with TestClient(api) as client:
        assert client.get("/health").json() == {"status": "ok"}
        for path in ("/docs", "/redoc", "/openapi.json", "/schema"):
            assert client.get(path).status_code == 404
        response = client.get("/capabilities")
        assert response.status_code == 503
        assert str(missing) not in response.text
    assert not missing.exists()


@pytest.mark.parametrize("role, employee_id", [("sales_analyst", None), ("sales_manager", None), ("account_manager", 1)])
def test_capabilities_include_only_selected_role_schema(settings, database, role, employee_id):
    with TestClient(create_app(settings)) as client:
        params = {"role": role}
        if employee_id is not None:
            params["employee_id"] = employee_id
        response = client.get("/capabilities", params=params)
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema"] == database.get_schema_for_user(UserContext("metadata", UserRole(role), employee_id))
    assert payload["role"] == role
    assert payload["app_mode"] == "public_demo"
    assert payload["max_result_rows"] == 3
    assert payload["default_provider"] == "demo"
    assert payload["allows_free_text"] is False
    assert payload["providers"] == [{
        "provider": "demo", "model_name": "secure-demo-generator", "requires_api_key": False, "supports_host": False,
    }]
    assert payload["ollama_host"] is None
    assert "api_key" not in payload
    assert response.headers["cache-control"] == "no-store"


def test_demo_query_is_identical_to_framework_neutral_response(settings):
    service = TextToSQLApplication(settings)
    expected = service.query(DEMO_BODY["question"], UserContext("am1", UserRole.ACCOUNT_MANAGER, 1))
    with TestClient(create_app(service=service)) as client:
        response = client.post("/query", json=DEMO_BODY)
    assert response.status_code == 200
    assert response.json() == json.loads(json.dumps(asdict(expected)))
    assert response.json()["success"]
    assert response.json()["row_level_security_applied"]
    assert response.json()["truncated"]
    assert response.json()["row_count"] == 3


def test_public_demo_ignores_provider_overrides_and_rejects_other_role_questions(settings):
    factory = Mock(side_effect=AssertionError("No cloud access in demo"))
    with TestClient(create_app(service=TextToSQLApplication(settings, provider_factory=factory))) as client:
        response = client.post("/query", json={
            **DEMO_BODY, "provider": "openai", "model_name": "ignored", "api_key": SECRET, "ollama_host": "invalid",
        })
        assert response.json()["success"] and response.json()["provider"] == "demo"
        assert SECRET not in response.text
        response = client.post("/query", json={**DEMO_BODY, "question": "Show customer count by account manager."})
        assert response.json()["error_type"] == "validation_error"
        assert not response.json()["success"]
    factory.assert_not_called()


@pytest.mark.parametrize("sql, error_type", [
    ("DELETE FROM customers", "unsafe_sql"),
    ("SELECT 1; SELECT 2", "unsafe_sql"),
    ("SELECT ( FROM products", "unsafe_sql"),
    ("SELECT employee_name FROM employees", "access_denied"),
    ("SELECT account_manager_id FROM customers", "access_denied"),
    ("SELECT * FROM customers", "access_denied"),
    ("SELECT name FROM sqlite_master", "access_denied"),
])
def test_http_queries_use_existing_security_before_execution(settings, provider, database, sql, error_type):
    provider.generate_sql.return_value = GeneratedSQL(sql, "Test.", "openai", "test-openai")
    with TestClient(create_app(service=local_application(settings, provider, database))) as client:
        response = client.post("/query", json=LOCAL_BODY)
    assert response.status_code == 200  # Domain result contract, including handled failures.
    payload = response.json()
    assert not payload["success"] and payload["error_type"] == error_type
    assert payload["rows"] == [] and payload["row_count"] == 0
    assert payload["generated_sql"] == payload["authorized_sql"] == ""
    database.execute_authorized.assert_not_called()
    assert SECRET not in response.text


def test_local_http_query_uses_real_adapter_filtered_schema_and_row_limits(settings, database):
    call = Mock(return_value=SimpleNamespace(output_text=json.dumps({
        "sql": "SELECT customer_id FROM customers ORDER BY customer_id", "explanation": "Assigned customers.",
    })))
    factory = Mock(side_effect=lambda **kwargs: OpenAITextToSQLProvider(
        kwargs["api_key"], kwargs["model_name"], client=SimpleNamespace(responses=SimpleNamespace(create=call)),
    ))
    service = TextToSQLApplication(
        replace(settings, app_mode=ApplicationMode.LOCAL_FULL, default_provider=ProviderType.OPENAI),
        provider_factory=factory, database_factory=lambda **kwargs: database,
    )
    with TestClient(create_app(service=service)) as client:
        response = client.post("/query", json={**DEMO_BODY, "question": "Arbitrary local question", "api_key": SECRET})
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] and payload["row_level_security_applied"]
    assert payload["row_count"] == 3 and payload["truncated"]
    assert payload["provider"] == "openai" and payload["model"] == "test-openai"
    assert database.execute_authorized.call_args.args[0].sql == payload["authorized_sql"]
    prompt = call.call_args.kwargs["input"]
    assert "Arbitrary local question" in prompt
    assert "Table: customers" in prompt
    assert "account_manager_id" not in prompt and "Table: employees" not in prompt
    assert SECRET not in prompt and SECRET not in response.text


@pytest.mark.parametrize("body", [
    {"api_key": SECRET},
    {**LOCAL_BODY, "role": SECRET},
    {**LOCAL_BODY, "employee_id": SECRET},
    {**LOCAL_BODY, "employee_id": True},
    {**LOCAL_BODY, "employee_id": 1.5},
    {**LOCAL_BODY, "provider": SECRET},
    {**LOCAL_BODY, "api_key": {"invalid": SECRET}},
    {**LOCAL_BODY, "question": {"invalid": SECRET}},
    {**LOCAL_BODY, "sql": SECRET},
    {**LOCAL_BODY, "max_result_rows": 10000},
    {**LOCAL_BODY, "app_mode": "local_full"},
    {**DEMO_BODY, "employee_id": None, "api_key": SECRET},
    {**DEMO_BODY, "employee_id": 0, "api_key": SECRET},
])
def test_invalid_http_requests_never_echo_inputs_or_invoke_service(settings, body):
    service = Mock(wraps=TextToSQLApplication(settings))
    with TestClient(create_app(service=service)) as client:
        response = client.post("/query", json=body)
    assert response.status_code == 422
    assert response.json() == {"error_type": "validation_error", "error_message": "The request is invalid."}
    assert SECRET not in response.text
    service.query.assert_not_called()


def test_malformed_json_and_metadata_validation_are_sanitized(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/query", content='{"api_key": "' + SECRET, headers={"Content-Type": "application/json"})
        assert response.status_code == 422 and SECRET not in response.text
        for params in ({"role": SECRET}, {"role": "account_manager"}, {"employee_id": -1}):
            response = client.get("/capabilities", params=params)
            assert response.status_code == 422 and SECRET not in response.text


def test_request_model_omits_api_key_from_repr_and_serialization():
    body = QueryBody(**LOCAL_BODY)
    assert SECRET not in repr(body)
    assert "api_key" not in body.model_dump()


def test_reflected_provider_key_never_reaches_http_response_or_logs(settings, provider, database, caplog):
    provider.generate_sql.return_value = GeneratedSQL(
        f"SELECT '{SECRET}' AS reflected", SECRET, "openai", "test-openai",
    )
    with TestClient(create_app(service=local_application(settings, provider, database))) as client:
        response = client.post("/query", json=LOCAL_BODY)
    assert response.json()["success"]
    assert response.json()["rows"] == [{"reflected": "[REDACTED]"}]
    assert SECRET not in response.text and SECRET not in caplog.text


def test_unexpected_error_does_not_escape_to_server_traceback_logger(settings, caplog):
    service = Mock(wraps=TextToSQLApplication(settings))
    service.query.side_effect = RuntimeError(SECRET)
    # Default raise_server_exceptions=True also proves the raw exception does not escape.
    with TestClient(create_app(service=service)) as client:
        response = client.post("/query", json=LOCAL_BODY)
    assert response.status_code == 500
    assert response.json() == {"error_type": "internal_error", "error_message": "The request could not be completed."}
    assert SECRET not in response.text and SECRET not in caplog.text


def test_query_completes_synchronously_before_response(settings, provider, database):
    completed = threading.Event()
    generated = provider.generate_sql.return_value
    def generate(*args):
        completed.set()
        return generated
    provider.generate_sql.side_effect = generate
    api = create_app(service=local_application(settings, provider, database))
    endpoint = next(route.endpoint for route in api.routes if route.path == "/query")
    assert not inspect.iscoroutinefunction(endpoint)
    with TestClient(api) as client:
        response = client.post("/query", json=LOCAL_BODY)
    assert completed.is_set()
    assert response.json()["success"]
    database.execute_authorized.assert_called_once()
