import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.create_sample_db import create_sample_database
from src.database import SQLiteReadOnlyDatabase
from src.models import TextToSQLRequest, TextToSQLResponse
from src.providers.base import TextToSQLProvider
from src.providers.models import (
    GeneratedSQL,
    LLMConfigurationError,
    LLMProviderError,
    LLMResponseError,
)
from src.rbac import RBACEnforcer, UserContext, UserRole
from src.service import TextToSQLService


SECRET = "sk-secret-value"


class FakeProvider(TextToSQLProvider):
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(
        self,
        sql: str = "SELECT customer_id, customer_name FROM customers ORDER BY customer_id",
        explanation: str = "Short explanation.",
        error: Exception | None = None,
    ) -> None:
        self.sql = sql
        self.explanation = explanation
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        self._validate_inputs(question, schema)
        self.calls.append((question, schema))
        if self.error is not None:
            raise self.error
        return GeneratedSQL(
            sql=self.sql,
            explanation=self.explanation,
            provider=self.provider_name,
            model=self.model_name,
        )


class SpyDatabase(SQLiteReadOnlyDatabase):
    def __init__(self, database_path: Path, max_result_rows: int = 200) -> None:
        super().__init__(database_path, max_result_rows=max_result_rows)
        self.executed_authorized_sql: list[str] = []
        self.provider_schema_calls = 0

    def get_schema_for_user(self, user: UserContext) -> str:
        self.provider_schema_calls += 1
        return super().get_schema_for_user(user)

    def execute_select(self, sql: str, parameters: tuple = ()):
        raise AssertionError("Generated SQL must not be executed directly")

    def execute_authorized(self, rbac_result, parameters: tuple = ()):
        self.executed_authorized_sql.append(rbac_result.sql)
        return super().execute_authorized(rbac_result, parameters)


class FailingExecuteDatabase(SpyDatabase):
    def execute_authorized(self, rbac_result, parameters: tuple = ()):
        raise sqlite3.OperationalError("database failure with secret " + SECRET)


@pytest.fixture()
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "company.db"
    create_sample_database(path)
    return path


@pytest.fixture()
def database(database_path: Path) -> SpyDatabase:
    return SpyDatabase(database_path)


def test_sales_analyst_successful_flow(database: SpyDatabase) -> None:
    provider = FakeProvider(
        sql="SELECT customer_id, customer_name FROM customers ORDER BY customer_id",
        explanation="Lists customer names.",
    )
    service = TextToSQLService(database, provider)
    request = TextToSQLRequest(
        question="List customer names",
        user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
    )

    response = service.answer(request)

    assert response.success is True
    assert response.provider == "fake"
    assert response.model == "fake-model"
    assert response.explanation == "Lists customer names."
    assert response.row_count == 20
    assert response.row_level_security_applied is False
    assert isinstance(response.rows[0], dict)
    assert response.guardrail_checks
    assert response.rbac_checks
    assert database.executed_authorized_sql == [response.authorized_sql]


def test_sales_manager_can_query_employees(database: SpyDatabase) -> None:
    provider = FakeProvider(sql="SELECT employee_id, employee_name FROM employees ORDER BY employee_id")
    service = TextToSQLService(database, provider)

    response = service.answer(
        TextToSQLRequest(
            question="List employees",
            user=UserContext(user_id="manager", role=UserRole.SALES_MANAGER),
        )
    )

    assert response.success is True
    assert response.row_count == 4
    assert response.row_level_security_applied is False


def test_account_manager_receives_only_assigned_customer_rows(
    database: SpyDatabase,
    database_path: Path,
) -> None:
    provider = FakeProvider(sql="SELECT customer_id, customer_name FROM customers ORDER BY customer_id")
    service = TextToSQLService(database, provider)
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)

    response = service.answer(TextToSQLRequest(question="List my customers", user=user))
    expected_ids = _assigned_customer_ids(database_path, employee_id=1)

    assert response.success is True
    assert {row["customer_id"] for row in response.rows} == expected_ids
    assert response.row_level_security_applied is True
    assert response.generated_sql != response.authorized_sql
    assert "__rbac_customers" in response.authorized_sql


def test_products_only_account_manager_query_is_not_changed(database: SpyDatabase) -> None:
    sql = "SELECT product_id, product_name FROM products ORDER BY product_id"
    provider = FakeProvider(sql=sql)
    service = TextToSQLService(database, provider)

    response = service.answer(
        TextToSQLRequest(
            question="List products",
            user=UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1),
        )
    )

    assert response.success is True
    assert response.row_level_security_applied is False
    assert response.generated_sql == sql
    assert response.authorized_sql == sql
    assert "__rbac_" not in response.authorized_sql


def test_result_truncation_is_preserved(database_path: Path) -> None:
    database = SpyDatabase(database_path, max_result_rows=3)
    provider = FakeProvider(sql="SELECT order_id FROM orders ORDER BY order_id")
    service = TextToSQLService(database, provider)

    response = service.answer(
        TextToSQLRequest(
            question="List orders",
            user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
        )
    )

    assert response.success is True
    assert response.row_count == 3
    assert response.truncated is True


def test_role_filtered_schema_is_sent_to_provider(database: SpyDatabase) -> None:
    provider = FakeProvider()
    service = TextToSQLService(database, provider)

    service.answer(
        TextToSQLRequest(
            question="List customers",
            user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
        )
    )

    sent_schema = provider.calls[0][1]
    assert "Table: employees" not in sent_schema
    assert "account_manager_id" not in sent_schema
    assert "Table: customers" in sent_schema


def test_account_manager_provider_schema_is_role_filtered(database: SpyDatabase) -> None:
    provider = FakeProvider()
    service = TextToSQLService(database, provider)

    service.answer(
        TextToSQLRequest(
            question="List customers",
            user=UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1),
        )
    )

    sent_schema = provider.calls[0][1]
    assert "Table: employees" not in sent_schema
    assert "account_manager_id" not in sent_schema


def test_generated_sql_is_never_executed_before_rbac(database: SpyDatabase) -> None:
    provider = FakeProvider(sql="SELECT customer_id FROM customers ORDER BY customer_id")
    service = TextToSQLService(database, provider)

    response = service.answer(
        TextToSQLRequest(
            question="List customers",
            user=UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1),
        )
    )

    assert response.success is True
    assert database.executed_authorized_sql == [response.authorized_sql]
    assert database.executed_authorized_sql[0] != response.generated_sql


@pytest.mark.parametrize(
    ("sql", "error_type"),
    [
        ("DROP TABLE customers", "unsafe_sql"),
        ("SELECT employee_name FROM employees", "access_denied"),
        ("SELECT account_manager_id FROM customers", "access_denied"),
    ],
)
def test_security_failures_map_safely(database: SpyDatabase, sql: str, error_type: str) -> None:
    service = TextToSQLService(database, FakeProvider(sql=sql))

    response = service.answer(
        TextToSQLRequest(
            question="Try something",
            user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
        )
    )

    assert response.success is False
    assert response.error_type == error_type
    assert SECRET not in (response.error_message or "")


def test_account_manager_cannot_bypass_row_level_security(
    database: SpyDatabase,
    database_path: Path,
) -> None:
    other_customer_id = _customer_id_for_other_manager(database_path, employee_id=1)
    provider = FakeProvider(sql=f"SELECT customer_id FROM customers WHERE customer_id = {other_customer_id}")
    service = TextToSQLService(database, provider)

    response = service.answer(
        TextToSQLRequest(
            question="Show another customer",
            user=UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1),
        )
    )

    assert response.success is True
    assert response.rows == []


def test_only_authorized_sql_reaches_execute_authorized(database: SpyDatabase) -> None:
    provider = FakeProvider(sql="SELECT customer_id FROM customers ORDER BY customer_id")
    service = TextToSQLService(database, provider)

    response = service.answer(
        TextToSQLRequest(
            question="List customers",
            user=UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1),
        )
    )

    assert database.executed_authorized_sql == [response.authorized_sql]


@pytest.mark.parametrize(
    ("error", "error_type"),
    [
        (LLMConfigurationError("bad config " + SECRET), "configuration_error"),
        (LLMProviderError("runtime " + SECRET), "provider_error"),
        (LLMResponseError("bad response " + SECRET), "invalid_provider_response"),
    ],
)
def test_provider_errors_map_safely(
    database: SpyDatabase,
    error: Exception,
    error_type: str,
) -> None:
    service = TextToSQLService(database, FakeProvider(error=error))

    response = service.answer(
        TextToSQLRequest(
            question="List customers",
            user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
        )
    )

    assert response.success is False
    assert response.error_type == error_type
    assert SECRET not in (response.error_message or "")


def test_database_failure_maps_safely(database_path: Path) -> None:
    database = FailingExecuteDatabase(database_path)
    service = TextToSQLService(database, FakeProvider())

    response = service.answer(
        TextToSQLRequest(
            question="List customers",
            user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
        )
    )

    assert response.success is False
    assert response.error_type == "database_error"
    assert SECRET not in (response.error_message or "")


def test_empty_question_maps_to_validation_error(database: SpyDatabase) -> None:
    service = TextToSQLService(database, FakeProvider())
    invalid_request = SimpleNamespace(
        question=" ",
        user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
    )

    response = service.answer(invalid_request)  # type: ignore[arg-type]

    assert response.success is False
    assert response.error_type == "validation_error"


def test_text_to_sql_response_model_validation() -> None:
    with pytest.raises(ValueError):
        TextToSQLResponse(
            success=True,
            question="Q",
            provider="fake",
            model="model",
            role=UserRole.SALES_ANALYST,
            generated_sql="SELECT 1",
            authorized_sql="SELECT 1",
            explanation="ok",
            error_type="provider_error",
            error_message="bad",
        )

    failed = TextToSQLResponse(
        success=False,
        question="Q",
        provider="fake",
        model="model",
        role=UserRole.SALES_ANALYST,
        generated_sql="",
        authorized_sql="",
        explanation="",
        rows=[{"x": 1}],
        row_count=1,
        truncated=True,
        error_type="validation_error",
        error_message="Invalid.",
    )
    assert failed.rows == []
    assert failed.row_count == 0
    assert failed.truncated is False
    assert failed.row_level_security_applied is False


def test_invalid_request_question_is_rejected() -> None:
    with pytest.raises(ValueError):
        TextToSQLRequest(
            question=" ",
            user=UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
        )


def _assigned_customer_ids(database_path: Path, employee_id: int) -> set[int]:
    with sqlite3.connect(database_path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT customer_id FROM customers WHERE account_manager_id = ?",
                (employee_id,),
            )
        }


def _customer_id_for_other_manager(database_path: Path, employee_id: int) -> int:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT customer_id
            FROM customers
            WHERE account_manager_id <> ?
            ORDER BY customer_id
            LIMIT 1
            """,
            (employee_id,),
        ).fetchone()
    return int(row[0])
