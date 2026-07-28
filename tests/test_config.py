import importlib
from pathlib import Path

import pytest

from src.models import TextToSQLResponse
from src.config import AppSettings, ApplicationMode
from src.providers import ProviderType
from src.rbac import UserRole


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in (
        "DATABASE_PATH",
        "APP_MODE",
        "MAX_RESULT_ROWS",
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OLLAMA_HOST",
        "OLLAMA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_default_settings_load_correctly() -> None:
    settings = AppSettings.from_env()

    assert settings.app_mode == ApplicationMode.PUBLIC_DEMO
    assert settings.is_public_demo is True
    assert settings.is_local_full is False
    assert settings.database_path == Path("data/company.db")
    assert settings.max_result_rows == 200
    assert settings.default_provider == ProviderType.DEMO
    assert settings.ollama_host == "http://localhost:11434"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PuBlIc_DeMo", ApplicationMode.PUBLIC_DEMO),
        ("LoCaL_FuLl", ApplicationMode.LOCAL_FULL),
    ],
)
def test_application_modes_are_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    expected: ApplicationMode,
) -> None:
    monkeypatch.setenv("APP_MODE", value)

    settings = AppSettings.from_env()

    assert settings.app_mode == expected
    assert settings.is_public_demo is (expected == ApplicationMode.PUBLIC_DEMO)
    assert settings.is_local_full is (expected == ApplicationMode.LOCAL_FULL)


def test_invalid_application_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "hosted_with_keys")

    with pytest.raises(ValueError, match="Unsupported APP_MODE"):
        AppSettings.from_env()


def test_custom_database_path_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", "custom/company.db")

    assert AppSettings.from_env().database_path == Path("custom/company.db")


def test_valid_max_result_rows_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_RESULT_ROWS", "500")

    assert AppSettings.from_env().max_result_rows == 500


@pytest.mark.parametrize("value", ["0", "-1", "10001", "not-an-int"])
def test_invalid_max_result_rows_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("MAX_RESULT_ROWS", value)

    with pytest.raises(ValueError):
        AppSettings.from_env()


def test_provider_names_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "local_full")
    monkeypatch.setenv("LLM_PROVIDER", "GeMiNi")

    assert AppSettings.from_env().default_provider == ProviderType.GEMINI


def test_unsupported_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "local_full")
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")

    with pytest.raises(ValueError):
        AppSettings.from_env()


def test_valid_ollama_host_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "local_full")
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.example.test")

    assert AppSettings.from_env().ollama_host == "https://ollama.example.test"


def test_invalid_ollama_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "local_full")
    monkeypatch.setenv("OLLAMA_HOST", "localhost:11434")

    with pytest.raises(ValueError):
        AppSettings.from_env()


def test_api_keys_are_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_MODE", "local_full")
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    settings = AppSettings.from_env()

    assert settings.default_provider == ProviderType.OPENAI


def test_public_demo_ignores_local_provider_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_MODE", "public_demo")
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")
    monkeypatch.setenv("OLLAMA_HOST", "not-a-url")
    monkeypatch.setenv("OPENAI_MODEL", "must-not-be-read")

    settings = AppSettings.from_env()

    assert settings.default_provider == ProviderType.DEMO
    assert settings.openai_model == ""
    assert settings.ollama_host == "http://localhost:11434"


def test_settings_representation_does_not_include_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key")

    assert "secret-key" not in repr(AppSettings.from_env())


def test_app_import_has_no_external_side_effects() -> None:
    module = importlib.import_module("app")

    assert hasattr(module, "main")


def test_role_display_name_mapping() -> None:
    module = importlib.import_module("app")

    assert module.get_role_display_name(UserRole.SALES_ANALYST) == "Sales Analyst"
    assert module.get_role_display_name(UserRole.SALES_MANAGER) == "Sales Manager"
    assert module.get_role_display_name(UserRole.ACCOUNT_MANAGER) == "Account Manager"


def test_provider_display_name_mapping() -> None:
    module = importlib.import_module("app")

    assert module.get_provider_display_name(ProviderType.OPENAI) == "OpenAI"
    assert module.get_provider_display_name(ProviderType.GEMINI) == "Gemini"
    assert module.get_provider_display_name(ProviderType.ANTHROPIC) == "Claude"
    assert module.get_provider_display_name(ProviderType.OLLAMA) == "Ollama"
    assert module.get_provider_display_name(ProviderType.DEMO) == "Secure Demo"


def test_example_question_update_contains_only_question_state() -> None:
    module = importlib.import_module("app")

    update = module.get_example_session_state_update("Show customer revenue")

    assert update == {"question_text": "Show customer revenue"}
    assert "api_key" not in update
    assert "OPENAI_API_KEY" not in update


def test_public_demo_disables_api_key_input_and_rejects_unknown_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_MODE", "public_demo")
    module = importlib.import_module("app")
    settings = AppSettings.from_env()
    user = module.UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)

    assert module.is_api_key_input_enabled(settings) is False
    assert module.get_ui_validation_message(
        "Invent an arbitrary query",
        ProviderType.DEMO,
        "secure-demo-generator",
        "",
        user,
        ApplicationMode.PUBLIC_DEMO,
    ) == "This public demo currently supports only the example questions shown in the sidebar."


def test_public_demo_constructs_demo_without_calling_cloud_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_MODE", "public_demo")
    module = importlib.import_module("app")
    settings = AppSettings.from_env()

    def fail_factory(**kwargs):
        raise AssertionError("Cloud provider factory must not be called")

    monkeypatch.setattr(module, "create_provider", fail_factory)
    provider = module.create_application_provider(
        settings,
        ProviderType.OPENAI,
        "ignored-model",
        "ignored-secret",
        "https://ignored.example",
    )

    assert provider.provider_name == "demo"
    assert not hasattr(provider, "_client")


def test_local_full_keeps_provider_factory_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_MODE", "local_full")
    module = importlib.import_module("app")
    settings = AppSettings.from_env()
    captured: dict[str, object] = {}

    def fake_factory(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(module, "create_provider", fake_factory)
    module.create_application_provider(
        settings,
        ProviderType.OPENAI,
        "local-model",
        "local-secret",
        "http://localhost:11434",
    )

    assert module.is_api_key_input_enabled(settings) is True
    assert captured["provider_type"] == ProviderType.OPENAI
    assert captured["api_key"] == "local-secret"


def test_local_provider_initialization_error_does_not_expose_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_MODE", "local_full")
    module = importlib.import_module("app")
    settings = AppSettings.from_env()
    secret = "local-secret-must-not-leak"

    def failing_factory(**kwargs):
        raise RuntimeError(f"SDK failed with {secret}")

    monkeypatch.setattr(module, "create_provider", failing_factory)
    with pytest.raises(module.LLMConfigurationError) as exc_info:
        module.create_application_provider(
            settings,
            ProviderType.OPENAI,
            "local-model",
            secret,
            "http://localhost:11434",
        )

    assert secret not in str(exc_info.value)


def test_csv_creation_includes_only_authorized_result_rows() -> None:
    module = importlib.import_module("app")
    rows = [{"customer_id": 1, "customer_name": "Blue Harbor Logistics"}]

    csv_text = module.create_results_csv(rows)

    assert "customer_id,customer_name" in csv_text
    assert "Blue Harbor Logistics" in csv_text
    assert "SELECT" not in csv_text
    assert "api_key" not in csv_text


def test_row_level_security_notice_depends_only_on_response_flag() -> None:
    module = importlib.import_module("app")
    base_kwargs = {
        "success": True,
        "question": "Q",
        "provider": "fake",
        "model": "fake",
        "role": UserRole.SALES_MANAGER,
        "generated_sql": "select 1",
        "authorized_sql": "SELECT 1",
        "explanation": "Explains the query.",
    }

    assert module.should_show_row_level_security_notice(
        TextToSQLResponse(**base_kwargs, row_level_security_applied=False)
    ) is False
    assert module.get_row_level_security_notice(
        TextToSQLResponse(**base_kwargs, row_level_security_applied=False)
    ) is None
    assert module.should_show_row_level_security_notice(
        TextToSQLResponse(**base_kwargs, row_level_security_applied=True)
    ) is True
    assert (
        module.get_row_level_security_notice(
            TextToSQLResponse(**base_kwargs, row_level_security_applied=True)
        )
        == "Row-level security was applied. Review the Authorized SQL tab to see the enforced data restrictions."
    )
