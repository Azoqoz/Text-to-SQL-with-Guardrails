import importlib
from pathlib import Path

import pytest

from src.models import TextToSQLResponse
from src.config import AppSettings
from src.providers import ProviderType
from src.rbac import UserRole


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    for key in (
        "DATABASE_PATH",
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

    assert settings.database_path == Path("data/company.db")
    assert settings.max_result_rows == 200
    assert settings.default_provider == ProviderType.OPENAI
    assert settings.ollama_host == "http://localhost:11434"


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
    monkeypatch.setenv("LLM_PROVIDER", "GeMiNi")

    assert AppSettings.from_env().default_provider == ProviderType.GEMINI


def test_unsupported_provider_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "unsupported")

    with pytest.raises(ValueError):
        AppSettings.from_env()


def test_valid_ollama_host_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "https://ollama.example.test")

    assert AppSettings.from_env().ollama_host == "https://ollama.example.test"


def test_invalid_ollama_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "localhost:11434")

    with pytest.raises(ValueError):
        AppSettings.from_env()


def test_api_keys_are_not_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    settings = AppSettings.from_env()

    assert settings.default_provider == ProviderType.OPENAI


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


def test_example_question_update_contains_only_question_state() -> None:
    module = importlib.import_module("app")

    update = module.get_example_session_state_update("Show customer revenue")

    assert update == {"question_text": "Show customer revenue"}
    assert "api_key" not in update
    assert "OPENAI_API_KEY" not in update


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
