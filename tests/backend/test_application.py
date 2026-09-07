import json
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.application import EXAMPLE_QUESTIONS, ApplicationError, ProviderOptions, TextToSQLApplication
from src.config import ApplicationMode
from src.models import TextToSQLRequest
from src.providers.demo_provider import DemoTextToSQLProvider
from src.providers.factory import ProviderType, create_provider
from src.providers.models import GeneratedSQL, LLMConfigurationError, LLMProviderError, LLMResponseError
from src.providers.openai_provider import OpenAITextToSQLProvider
from src.rbac import UserContext, UserRole
from src.service import TextToSQLService


ANALYST = UserContext("analyst", UserRole.SALES_ANALYST)
ACCOUNT_MANAGER = UserContext("am1", UserRole.ACCOUNT_MANAGER, 1)
SECRET = "sk-backend-test-never-persist-or-return"


def local_settings(settings):
    return replace(settings, app_mode=ApplicationMode.LOCAL_FULL, default_provider=ProviderType.OPENAI)


def test_application_import_does_not_load_ui_or_http_framework():
    result = subprocess.run(
        [sys.executable, "-B", "-c", "import sys; import src.application; "
         "assert not {'streamlit', 'fastapi', 'app'} & sys.modules.keys()"],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_example_catalog_matches_unchanged_streamlit():
    import app

    assert {role: list(questions) for role, questions in EXAMPLE_QUESTIONS.items()} == app.EXAMPLE_QUESTIONS


@pytest.mark.parametrize("role, question", [
    (role, question) for role, questions in EXAMPLE_QUESTIONS.items() for question in questions
])
def test_demo_results_match_existing_service_exactly(settings, database, role, question):
    user = UserContext("demo", role, 1 if role == UserRole.ACCOUNT_MANAGER else None)
    expected = TextToSQLService(database, DemoTextToSQLProvider()).answer(TextToSQLRequest(question, user))
    factory = Mock(side_effect=AssertionError("Public demo must never create an external provider"))
    application = TextToSQLApplication(settings, provider_factory=factory)
    actual = application.query(question, user, ProviderOptions(ProviderType.OPENAI, "ignored", SECRET, "invalid"))
    assert actual == expected
    assert actual.success
    factory.assert_not_called()


@pytest.mark.parametrize("question", ["", "Invent a query", "Show customer count by account manager."])
def test_demo_rejects_questions_outside_selected_role_before_execution(settings, database, question):
    application = TextToSQLApplication(settings, database_factory=lambda **kwargs: database)
    response = application.query(question, ACCOUNT_MANAGER)
    assert response.error_type == "validation_error"
    assert not response.success
    database.execute_authorized.assert_not_called()


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("sql", [
    "select customer_id from customers order by customer_id;",
    "WITH chosen AS (SELECT order_id FROM orders) SELECT order_id FROM chosen ORDER BY order_id",
    "SELECT order_item_id FROM order_items ORDER BY order_item_id",
    "SELECT customer_id FROM customers WHERE customer_id = 2 OR 1 = 1 ORDER BY customer_id",
    "SELECT product_id FROM products UNION SELECT product_id FROM products ORDER BY product_id",
    "SELECT employee_name FROM employees ORDER BY employee_id",
    "SELECT account_manager_id FROM customers",
    "SELECT * FROM customers", "SELECT COUNT(*) FROM customers",
    "SELECT name FROM sqlite_master", "SELECT salary FROM payroll",
    "DELETE FROM customers", "PRAGMA table_info(customers)",
    "SELECT 1; SELECT 2", "SELECT ( FROM customers", "SELECT 1 -- comment",
    "SELECT nonexistent_column FROM products",
])
def test_local_results_and_errors_match_existing_pipeline(settings, database, provider, role, sql):
    user = UserContext("local", role, 1 if role == UserRole.ACCOUNT_MANAGER else None)
    provider.generate_sql.return_value = GeneratedSQL(sql, "Parity.", "openai", "test-openai")
    expected = TextToSQLService(database, provider).answer(TextToSQLRequest("List data", user))
    database.execute_authorized.reset_mock()
    application = TextToSQLApplication(
        local_settings(settings), provider_factory=lambda **kwargs: provider,
        database_factory=lambda **kwargs: database,
    )
    before = settings.database_path.read_bytes()
    actual = application.query("List data", user, ProviderOptions(api_key=SECRET))
    assert actual == expected
    if actual.error_type in {"unsafe_sql", "access_denied"}:
        database.execute_authorized.assert_not_called()
    elif actual.success:
        database.execute_authorized.assert_called_once()
        assert database.execute_authorized.call_args.args[0].sql == actual.authorized_sql
    assert settings.database_path.read_bytes() == before


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("mode", list(ApplicationMode))
def test_capabilities_use_existing_role_schema_and_mode(settings, database, role, mode):
    settings = replace(settings, app_mode=mode, default_provider=ProviderType.OPENAI)
    user = UserContext("metadata", role, 1 if role == UserRole.ACCOUNT_MANAGER else None)
    application = TextToSQLApplication(settings)
    capabilities = application.capabilities(user)
    assert capabilities.schema == database.get_schema_for_user(user)
    assert capabilities.max_result_rows == settings.max_result_rows
    assert capabilities.role == role
    assert capabilities.app_mode == mode
    if mode == ApplicationMode.PUBLIC_DEMO:
        assert capabilities.default_provider == ProviderType.DEMO
        assert [item.provider for item in capabilities.providers] == [ProviderType.DEMO]
        assert not capabilities.allows_free_text and capabilities.ollama_host is None
        assert capabilities.providers[0].model_name == DemoTextToSQLProvider.model_name
    else:
        assert capabilities.default_provider == ProviderType.OPENAI
        assert [item.provider for item in capabilities.providers] == [
            ProviderType.OPENAI, ProviderType.GEMINI, ProviderType.ANTHROPIC, ProviderType.OLLAMA,
        ]
        assert capabilities.allows_free_text
        assert [item.requires_api_key for item in capabilities.providers] == [True, True, True, False]
        assert [item.supports_host for item in capabilities.providers] == [False, False, False, True]
    assert {item.role: item.requires_employee_id for item in capabilities.roles} == {
        UserRole.SALES_ANALYST: False, UserRole.SALES_MANAGER: False, UserRole.ACCOUNT_MANAGER: True,
    }
    assert "api_key" not in asdict(capabilities)


def test_missing_database_has_safe_errors_and_is_not_created(settings, tmp_path):
    missing = tmp_path / "private-missing.db"
    application = TextToSQLApplication(replace(settings, database_path=missing))
    response = application.query(EXAMPLE_QUESTIONS[ANALYST.role][0], ANALYST)
    assert response.error_type == "database_error"
    assert str(missing) not in response.error_message
    with pytest.raises(ApplicationError) as error:
        application.capabilities(ANALYST)
    assert error.value.error_type == "database_error"
    assert str(missing) not in str(error.value)
    assert not missing.exists()


@pytest.mark.parametrize("kind", [ProviderType.OPENAI, ProviderType.GEMINI, ProviderType.ANTHROPIC, ProviderType.OLLAMA])
def test_local_factory_receives_only_current_request_configuration(settings, provider, kind):
    factory = Mock(return_value=provider)
    application = TextToSQLApplication(local_settings(settings), provider_factory=factory)
    options = ProviderOptions(kind, "override-model", SECRET, "http://localhost:12345")
    response = application.query("List customers", ANALYST, options)
    assert response.success
    factory.assert_called_once_with(
        provider_type=kind, model_name="override-model", api_key=SECRET, ollama_host="http://localhost:12345",
    )
    assert SECRET not in repr(options)
    assert SECRET not in json.dumps(asdict(response))


def test_credentials_and_provider_instances_are_not_reused(settings):
    calls = []
    def factory(**kwargs):
        calls.append(kwargs["api_key"])
        if kwargs["api_key"] is None:
            raise LLMConfigurationError("Key is required")
        client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(
            output_text='{"sql": "SELECT product_id FROM products ORDER BY product_id"}',
        )))
        return OpenAITextToSQLProvider(kwargs["api_key"], kwargs["model_name"], client=client)

    application = TextToSQLApplication(local_settings(settings), provider_factory=factory)
    assert application.query("List products", ANALYST, ProviderOptions(api_key=SECRET)).success
    assert application.query("List products", ANALYST).error_type == "configuration_error"
    assert calls == [SECRET, None]
    assert SECRET not in repr(vars(application))


@pytest.mark.parametrize("error", [LLMConfigurationError(SECRET), RuntimeError(SECRET)])
def test_initialization_errors_are_sanitized_without_fallback(settings, error):
    factory = Mock(side_effect=error)
    application = TextToSQLApplication(local_settings(settings), provider_factory=factory)
    response = application.query("List products", ANALYST, ProviderOptions(api_key=SECRET))
    assert not response.success and response.error_type == "configuration_error"
    assert SECRET not in json.dumps(asdict(response))
    factory.assert_called_once()


@pytest.mark.parametrize("error, error_type", [
    (LLMProviderError(SECRET), "provider_error"), (LLMResponseError(SECRET), "invalid_provider_response"),
])
def test_provider_failure_preserves_safe_service_contract(settings, database, provider, error, error_type):
    provider.generate_sql.side_effect = error
    application = TextToSQLApplication(
        local_settings(settings), provider_factory=lambda **kwargs: provider,
        database_factory=lambda **kwargs: database,
    )
    response = application.query("List products", ANALYST, ProviderOptions(api_key=SECRET))
    assert response.error_type == error_type
    assert response.rows == [] and response.generated_sql == response.authorized_sql == ""
    assert SECRET not in json.dumps(asdict(response))
    provider.generate_sql.assert_called_once()
    database.execute_authorized.assert_not_called()


def test_missing_cloud_key_uses_existing_factory_validation(settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    application = TextToSQLApplication(local_settings(settings), provider_factory=create_provider)
    response = application.query("List customers", ANALYST)
    assert response.error_type == "configuration_error"  # Never falls back to ambient credentials.


@pytest.mark.parametrize("use_default", [False, True])
def test_local_mode_preserves_explicit_demo_factory_support(settings, database, use_default):
    configured = replace(
        local_settings(settings), default_provider=ProviderType.DEMO if use_default else ProviderType.OPENAI,
    )
    application = TextToSQLApplication(configured)
    options = ProviderOptions() if use_default else ProviderOptions(provider=ProviderType.DEMO)
    question = EXAMPLE_QUESTIONS[ACCOUNT_MANAGER.role][0]
    expected = TextToSQLService(database, DemoTextToSQLProvider()).answer(TextToSQLRequest(question, ACCOUNT_MANAGER))
    assert application.query(question, ACCOUNT_MANAGER, options) == expected
    if use_default:
        capabilities = application.capabilities(ACCOUNT_MANAGER)
        assert capabilities.default_provider in {item.provider for item in capabilities.providers}


def test_reflected_key_is_redacted_from_every_response_field_without_writes(settings, provider, tmp_path):
    provider.generate_sql.return_value = GeneratedSQL(
        f"SELECT '{SECRET}' AS reflected", f"Explanation: {SECRET}", "openai", "test-openai",
    )
    application = TextToSQLApplication(local_settings(settings), provider_factory=lambda **kwargs: provider)
    before = {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()}
    response = application.query(f"List {SECRET}", ANALYST, ProviderOptions(api_key=SECRET))
    assert response.success
    assert response.rows == [{"reflected": "[REDACTED]"}]
    assert SECRET not in json.dumps(asdict(response))
    assert {path: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()} == before
