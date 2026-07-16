import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.providers.anthropic_provider import AnthropicTextToSQLProvider
from src.providers.base import TextToSQLProvider
from src.providers.factory import ProviderType, create_provider
from src.providers.gemini_provider import GeminiTextToSQLProvider
from src.providers.models import (
    GeneratedSQL,
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseError,
    parse_generated_sql_response,
)
from src.providers.ollama_provider import OllamaTextToSQLProvider
from src.providers.openai_provider import OpenAITextToSQLProvider
from src.providers.prompt import build_text_to_sql_prompt


SCHEMA = "Table: customers\nColumns:\n- customer_id (INTEGER, primary key)\n- customer_name (TEXT)"
QUESTION = "List customers by name"
JSON_RESPONSE = '{"sql": "SELECT customer_name FROM customers ORDER BY customer_name", "explanation": "Lists customers."}'
SECRET_KEY = "sk-test-secret-should-not-leak"


def test_shared_prompt_contains_required_instructions() -> None:
    prompt = build_text_to_sql_prompt(QUESTION, SCHEMA)

    assert SCHEMA in prompt
    assert QUESTION in prompt
    assert "JSON only" in prompt
    assert "SQLite" in prompt
    assert "INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, REPLACE" in prompt
    assert "Never invent tables or columns" in prompt
    assert "Avoid SELECT *" in prompt
    assert "DATABASE SCHEMA" in prompt
    assert "USER QUESTION" in prompt


def test_parse_generated_sql_response_parses_valid_json() -> None:
    generated = parse_generated_sql_response(JSON_RESPONSE, "fake", "fake-model")

    assert generated == GeneratedSQL(
        sql="SELECT customer_name FROM customers ORDER BY customer_name",
        explanation="Lists customers.",
        provider="fake",
        model="fake-model",
    )


def test_parse_generated_sql_response_parses_fenced_json() -> None:
    generated = parse_generated_sql_response(f"```json\n{JSON_RESPONSE}\n```", "fake", "fake-model")

    assert generated.sql.startswith("SELECT")


@pytest.mark.parametrize("text", ["not json", "[1, 2, 3]", '{"explanation": "missing sql"}'])
def test_parse_generated_sql_response_rejects_invalid_content(text: str) -> None:
    with pytest.raises(LLMResponseError):
        parse_generated_sql_response(text, "fake", "fake-model")


def test_generated_sql_defaults_empty_explanation() -> None:
    generated = GeneratedSQL(sql="SELECT 1", explanation="", provider="fake", model="model")

    assert generated.explanation


class FakeProvider(TextToSQLProvider):
    provider_name = "fake"
    model_name = "fake-model"

    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        self._validate_inputs(question, schema)
        return GeneratedSQL(sql="SELECT 1", explanation="", provider=self.provider_name, model=self.model_name)


def test_base_input_validation_rejects_empty_question_and_schema() -> None:
    provider = FakeProvider()

    with pytest.raises(LLMConfigurationError):
        provider.generate_sql("", SCHEMA)
    with pytest.raises(LLMConfigurationError):
        provider.generate_sql(QUESTION, "")


def test_factory_creates_all_provider_types() -> None:
    assert create_provider(ProviderType.OPENAI, "model", api_key="key").provider_name == "openai"
    assert create_provider(ProviderType.GEMINI, "model", api_key="key").provider_name == "gemini"
    assert create_provider(ProviderType.ANTHROPIC, "model", api_key="key").provider_name == "anthropic"
    assert create_provider(ProviderType.OLLAMA, "model").provider_name == "ollama"


def test_factory_accepts_case_insensitive_provider_strings() -> None:
    provider = create_provider("OpEnAi", "model", api_key="key")

    assert provider.provider_name == "openai"


def test_factory_rejects_unsupported_provider() -> None:
    with pytest.raises(LLMConfigurationError):
        create_provider("unsupported", "model")


@pytest.mark.parametrize("provider_type", [ProviderType.OPENAI, ProviderType.GEMINI, ProviderType.ANTHROPIC])
def test_factory_requires_api_keys_for_cloud_providers(provider_type: ProviderType) -> None:
    with pytest.raises(LLMConfigurationError):
        create_provider(provider_type, "model")


def test_factory_does_not_require_key_for_ollama() -> None:
    provider = create_provider("ollama", "local-model")

    assert provider.provider_name == "ollama"


def test_factory_does_not_expose_api_key_in_exception_text() -> None:
    with pytest.raises(LLMConfigurationError) as exc_info:
        create_provider("openai", "", api_key=SECRET_KEY)

    assert SECRET_KEY not in str(exc_info.value)


class FakeOpenAIResponses:
    def __init__(self, response: object | None = None, failure: Exception | None = None) -> None:
        self.response = response or SimpleNamespace(output_text=JSON_RESPONSE)
        self.failure = failure
        self.last_prompt = ""

    def create(self, model: str, input: str) -> object:
        self.last_prompt = input
        if self.failure is not None:
            raise self.failure
        return self.response


def test_openai_provider_uses_injected_client_and_extracts_output_text() -> None:
    responses = FakeOpenAIResponses()
    client = SimpleNamespace(responses=responses)
    provider = OpenAITextToSQLProvider(api_key="key", model_name="model", client=client)

    generated = provider.generate_sql(QUESTION, SCHEMA)

    assert generated.provider == "openai"
    assert generated.model == "model"
    assert generated.sql.startswith("SELECT")
    assert QUESTION in responses.last_prompt


def test_openai_provider_wraps_failures_safely() -> None:
    client = SimpleNamespace(responses=FakeOpenAIResponses(failure=RuntimeError(SECRET_KEY)))
    provider = OpenAITextToSQLProvider(api_key=SECRET_KEY, model_name="model", client=client)

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate_sql(QUESTION, SCHEMA)

    assert SECRET_KEY not in str(exc_info.value)


class FakeGeminiModels:
    def __init__(self, response: object | None = None, failure: Exception | None = None) -> None:
        self.response = response or SimpleNamespace(text=JSON_RESPONSE)
        self.failure = failure
        self.last_contents = ""

    def generate_content(self, model: str, contents: str) -> object:
        self.last_contents = contents
        if self.failure is not None:
            raise self.failure
        return self.response


def test_gemini_provider_uses_injected_client_and_extracts_text() -> None:
    models = FakeGeminiModels()
    client = SimpleNamespace(models=models)
    provider = GeminiTextToSQLProvider(api_key="key", model_name="model", client=client)

    generated = provider.generate_sql(QUESTION, SCHEMA)

    assert generated.provider == "gemini"
    assert generated.sql.startswith("SELECT")
    assert SCHEMA in models.last_contents


def test_gemini_provider_wraps_failures_safely() -> None:
    client = SimpleNamespace(models=FakeGeminiModels(failure=RuntimeError(SECRET_KEY)))
    provider = GeminiTextToSQLProvider(api_key=SECRET_KEY, model_name="model", client=client)

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate_sql(QUESTION, SCHEMA)

    assert SECRET_KEY not in str(exc_info.value)


class FakeAnthropicMessages:
    def __init__(self, response: object | None = None, failure: Exception | None = None) -> None:
        self.response = response or SimpleNamespace(
            content=[
                SimpleNamespace(text='{"sql": "SELECT customer_name '),
                SimpleNamespace(text='FROM customers", "explanation": "Lists customers."}'),
            ]
        )
        self.failure = failure
        self.last_messages: list[dict[str, str]] = []

    def create(self, **kwargs):
        self.last_messages = kwargs["messages"]
        if self.failure is not None:
            raise self.failure
        return self.response


def test_anthropic_provider_combines_text_blocks() -> None:
    messages = FakeAnthropicMessages()
    client = SimpleNamespace(messages=messages)
    provider = AnthropicTextToSQLProvider(api_key="key", model_name="model", client=client)

    generated = provider.generate_sql(QUESTION, SCHEMA)

    assert generated.provider == "anthropic"
    assert generated.sql == "SELECT customer_name FROM customers"
    assert QUESTION in messages.last_messages[0]["content"]


def test_anthropic_provider_validates_max_tokens() -> None:
    with pytest.raises(LLMConfigurationError):
        AnthropicTextToSQLProvider(api_key="key", model_name="model", max_tokens=0, client=object())


def test_anthropic_provider_wraps_failures_safely() -> None:
    client = SimpleNamespace(messages=FakeAnthropicMessages(failure=RuntimeError(SECRET_KEY)))
    provider = AnthropicTextToSQLProvider(api_key=SECRET_KEY, model_name="model", client=client)

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate_sql(QUESTION, SCHEMA)

    assert SECRET_KEY not in str(exc_info.value)


class FakeOllamaClient:
    def __init__(self, response: object | None = None, failure: Exception | None = None) -> None:
        self.response = response or {"message": {"content": JSON_RESPONSE}}
        self.failure = failure
        self.last_messages: list[dict[str, str]] = []

    def chat(self, model: str, messages: list[dict[str, str]], stream: bool) -> object:
        self.last_messages = messages
        assert stream is False
        if self.failure is not None:
            raise self.failure
        return self.response


def test_ollama_provider_extracts_assistant_message_content() -> None:
    client = FakeOllamaClient()
    provider = OllamaTextToSQLProvider(model_name="model", client=client)

    generated = provider.generate_sql(QUESTION, SCHEMA)

    assert generated.provider == "ollama"
    assert generated.sql.startswith("SELECT")
    assert SCHEMA in client.last_messages[0]["content"]


def test_ollama_provider_validates_host() -> None:
    with pytest.raises(LLMConfigurationError):
        OllamaTextToSQLProvider(model_name="model", host="localhost:11434", client=object())


def test_ollama_provider_handles_unavailable_server_safely() -> None:
    provider = OllamaTextToSQLProvider(
        model_name="model",
        client=FakeOllamaClient(failure=ConnectionError(SECRET_KEY)),
    )

    with pytest.raises(LLMProviderError) as exc_info:
        provider.generate_sql(QUESTION, SCHEMA)

    assert "local Ollama server" in str(exc_info.value)
    assert SECRET_KEY not in str(exc_info.value)


def test_provider_modules_do_not_import_database_execution_layer() -> None:
    provider_files = (PROJECT_ROOT / "src" / "providers").glob("*.py")

    for path in provider_files:
        assert "src.database" not in path.read_text()


def test_providers_do_not_require_real_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OLLAMA_MODEL"):
        monkeypatch.delenv(key, raising=False)

    provider = OpenAITextToSQLProvider(
        api_key="test-key",
        model_name="test-model",
        client=SimpleNamespace(responses=FakeOpenAIResponses()),
    )

    assert provider.generate_sql(QUESTION, SCHEMA).sql.startswith("SELECT")
