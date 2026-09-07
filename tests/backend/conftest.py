import socket
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.create_sample_db import create_sample_database
from src.config import AppSettings, ApplicationMode
from src.database import SQLiteReadOnlyDatabase
from src.providers.base import TextToSQLProvider
from src.providers.anthropic_provider import AnthropicTextToSQLProvider
from src.providers.factory import ProviderType
from src.providers.gemini_provider import GeminiTextToSQLProvider
from src.providers.models import GeneratedSQL
from src.providers.ollama_provider import OllamaTextToSQLProvider
from src.providers.openai_provider import OpenAITextToSQLProvider


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("Backend tests must not contact external providers")

    # Windows asyncio uses a loopback socketpair internally for TestClient.
    # Block network client construction rather than that event-loop mechanism.
    monkeypatch.setattr(socket, "create_connection", reject_network)
    for provider in (
        OpenAITextToSQLProvider, GeminiTextToSQLProvider,
        AnthropicTextToSQLProvider, OllamaTextToSQLProvider,
    ):
        monkeypatch.setattr(provider, "_create_client", reject_network)


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "company.db"
    create_sample_database(path)
    return AppSettings(
        app_mode=ApplicationMode.PUBLIC_DEMO, database_path=path, max_result_rows=3,
        default_provider=ProviderType.DEMO, openai_model="test-openai", gemini_model="test-gemini",
        anthropic_model="test-anthropic", ollama_model="test-ollama", ollama_host="http://localhost:11434",
    )


@pytest.fixture
def database(settings):
    database = SQLiteReadOnlyDatabase(settings.database_path, settings.max_result_rows)
    database.execute_authorized = Mock(wraps=database.execute_authorized)
    return database


@pytest.fixture
def provider():
    provider = Mock(spec=TextToSQLProvider)
    provider.provider_name = "openai"
    provider.model_name = "test-openai"
    provider.generate_sql.return_value = GeneratedSQL(
        "SELECT customer_id FROM customers ORDER BY customer_id", "Lists customers.", "openai", "test-openai",
    )
    return provider
