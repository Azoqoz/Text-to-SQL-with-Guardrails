"""Mode selection and real provider adapters with injected, offline SDK clients."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app
import src.config as config
from src.config import AppSettings, ApplicationMode
from src.models import TextToSQLRequest
from src.providers.anthropic_provider import AnthropicTextToSQLProvider
from src.providers.demo_provider import DemoTextToSQLProvider
from src.providers.factory import ProviderType
from src.providers.gemini_provider import GeminiTextToSQLProvider
from src.providers.models import LLMConfigurationError
from src.providers.ollama_provider import OllamaTextToSQLProvider
from src.providers.openai_provider import OpenAITextToSQLProvider
from src.rbac import UserContext, UserRole
from src.service import TextToSQLService


SQL = "SELECT customer_id FROM customers ORDER BY customer_id"
QUESTION = "Show my assigned customers."
USER = UserContext("account-manager", UserRole.ACCOUNT_MANAGER, 1)


@pytest.fixture
def settings_from_env(monkeypatch, database):
    # Neither a developer's .env nor ambient provider settings affect the oracle.
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for key in ("APP_MODE", "LLM_PROVIDER", "MAX_RESULT_ROWS", "OPENAI_MODEL", "GEMINI_MODEL",
                "ANTHROPIC_MODEL", "OLLAMA_MODEL", "OLLAMA_HOST"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(database.database_path))
    return AppSettings.from_env


def test_default_demo_ignores_external_configuration_and_executes_offline(
    settings_from_env, monkeypatch, database,
):
    monkeypatch.setenv("LLM_PROVIDER", "invalid-provider")
    monkeypatch.setenv("OPENAI_MODEL", "must-be-ignored")
    monkeypatch.setenv("OLLAMA_HOST", "invalid-host")
    settings = settings_from_env()
    assert settings.app_mode == ApplicationMode.PUBLIC_DEMO
    assert settings.default_provider == ProviderType.DEMO
    assert settings.max_result_rows == 200
    assert (settings.openai_model, settings.gemini_model, settings.anthropic_model, settings.ollama_model) == ("", "", "", "")
    factory = Mock(side_effect=AssertionError("Demo must not construct a cloud provider"))
    monkeypatch.setattr(app, "create_provider", factory)
    provider = app.create_application_provider(settings, ProviderType.OPENAI, "ignored", "ignored", "invalid")
    response = TextToSQLService(database, provider).answer(TextToSQLRequest(QUESTION, USER))
    assert response.success, response.error_message
    assert response.provider == "demo"
    assert response.model == "secure-demo-generator"
    assert response.rows == [
        {"customer_id": 1, "customer_name": "Alpha", "region": "East", "signup_date": "2025-01-01"},
        {"customer_id": 3, "customer_name": "Gamma", "region": "East", "signup_date": "2025-01-01"},
    ]
    assert response.row_level_security_applied
    factory.assert_not_called()


@pytest.mark.parametrize("question, accepted", [
    (QUESTION, True), ("Show customer count by account manager.", False), ("Invent a query", False),
])
def test_demo_question_allowlist_is_role_specific_while_local_accepts_free_text(question, accepted):
    demo_message = app.get_ui_validation_message(
        question, ProviderType.DEMO, "", "", USER, ApplicationMode.PUBLIC_DEMO,
    )
    assert (demo_message is None) is accepted
    assert app.get_ui_validation_message(
        question, ProviderType.OLLAMA, "local-model", "", USER, ApplicationMode.LOCAL_FULL,
    ) is None


def test_demo_unknown_question_returns_error_without_fallback(database):
    response = TextToSQLService(database, DemoTextToSQLProvider()).answer(TextToSQLRequest("Invent a query", USER))
    assert not response.success
    assert response.error_type == "invalid_provider_response"
    assert response.error_message == "The provider returned an invalid SQL response."
    assert response.rows == []
    database.execute_authorized.assert_not_called()


@pytest.mark.parametrize("kind", ["openai", "gemini", "anthropic", "ollama"])
def test_local_mode_forwards_selected_provider_configuration(settings_from_env, monkeypatch, kind):
    monkeypatch.setenv("APP_MODE", "local_full")
    monkeypatch.setenv("LLM_PROVIDER", kind)
    monkeypatch.setenv("MAX_RESULT_ROWS", "7")
    monkeypatch.setenv(f"{kind.upper()}_MODEL", "configured-model")
    settings = settings_from_env()
    assert settings.is_local_full
    assert settings.default_provider == ProviderType(kind)
    assert settings.max_result_rows == 7
    assert getattr(settings, f"{kind}_model") == "configured-model"
    factory = Mock(return_value=object())
    monkeypatch.setattr(app, "create_provider", factory)
    selected = app.create_application_provider(settings, ProviderType(kind), "selected-model", "test-key", "http://localhost:11434")
    assert selected is factory.return_value
    factory.assert_called_once_with(
        provider_type=ProviderType(kind), model_name="selected-model", api_key="test-key",
        ollama_host="http://localhost:11434",
    )


def test_local_initialization_failure_does_not_fall_back_to_demo(settings_from_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "local_full")
    settings = settings_from_env()
    assert settings.default_provider == ProviderType.OPENAI
    factory = Mock(side_effect=RuntimeError("private SDK detail"))
    demo = Mock(side_effect=AssertionError("No automatic demo fallback"))
    monkeypatch.setattr(app, "create_provider", factory)
    monkeypatch.setattr(app, "DemoTextToSQLProvider", demo)
    with pytest.raises(LLMConfigurationError, match="Selected provider could not be initialized") as error:
        app.create_application_provider(settings, ProviderType.OPENAI, "model", "test-key", "http://localhost:11434")
    assert "private SDK detail" not in str(error.value)
    factory.assert_called_once()
    demo.assert_not_called()


def injected_provider(kind, payload, failure=None):
    call = Mock(side_effect=failure)
    if kind == "openai":
        call.return_value = SimpleNamespace(output_text=payload)
        provider = OpenAITextToSQLProvider("test-key", "test-model", client=SimpleNamespace(responses=SimpleNamespace(create=call)))
    elif kind == "gemini":
        call.return_value = SimpleNamespace(text=payload)
        provider = GeminiTextToSQLProvider("test-key", "test-model", client=SimpleNamespace(models=SimpleNamespace(generate_content=call)))
    elif kind == "anthropic":
        call.return_value = SimpleNamespace(content=[SimpleNamespace(text=payload)])
        provider = AnthropicTextToSQLProvider("test-key", "test-model", client=SimpleNamespace(messages=SimpleNamespace(create=call)))
    else:
        call.return_value = {"message": {"content": payload}}
        provider = OllamaTextToSQLProvider("test-model", client=SimpleNamespace(chat=call))
    return provider, call


@pytest.mark.parametrize("kind", ["openai", "gemini", "anthropic", "ollama"])
@pytest.mark.parametrize("outcome", ["success", "invalid-json", "unsafe-sql", "provider-error"])
def test_provider_adapters_preserve_pipeline_contract(database, kind, outcome):
    payload = json.dumps({"sql": "DELETE FROM customers" if outcome == "unsafe-sql" else SQL})
    if outcome == "invalid-json":
        payload = "invalid JSON private SDK detail"
    failure = ConnectionError("private SDK detail") if outcome == "provider-error" else None
    provider, call = injected_provider(kind, payload, failure)
    response = TextToSQLService(database, provider).answer(TextToSQLRequest(QUESTION, USER))
    call.assert_called_once()  # No implicit retry/fallback at the adapter/service boundary.
    assert response.provider == kind and response.model == "test-model"
    arguments = call.call_args.kwargs
    prompt = arguments.get("input") or arguments.get("contents") or arguments["messages"][0]["content"]
    assert QUESTION in prompt
    assert "Table: customers" in prompt
    assert "account_manager_id" not in prompt and "Table: employees" not in prompt
    if outcome == "success":
        assert response.success, response.error_message
        assert response.rows == [{"customer_id": 1}, {"customer_id": 3}]
        assert response.generated_sql == SQL
        assert response.row_level_security_applied
        assert response.explanation == "SQL generated from the supplied schema and question."
        database.execute_authorized.assert_called_once()
    else:
        assert not response.success
        assert response.error_type == {
            "invalid-json": "invalid_provider_response", "unsafe-sql": "unsafe_sql", "provider-error": "provider_error",
        }[outcome]
        assert "private SDK detail" not in response.error_message
        assert response.generated_sql == response.authorized_sql == ""
        assert response.rows == [] and response.row_count == 0
        database.execute_authorized.assert_not_called()


def test_openai_nested_output_fallback_and_fenced_json_execute(database):
    provider, call = injected_provider("openai", "")
    payload = "```json\n" + json.dumps({"sql": SQL, "explanation": "Lists assigned IDs."}) + "\n```"
    call.return_value = SimpleNamespace(output_text="", output=[
        SimpleNamespace(content=[SimpleNamespace(text=payload[:20]), SimpleNamespace(text=payload[20:])]),
    ])
    response = TextToSQLService(database, provider).answer(TextToSQLRequest(QUESTION, USER))
    assert response.success, response.error_message
    assert response.rows == [{"customer_id": 1}, {"customer_id": 3}]
    assert response.explanation == "Lists assigned IDs."
