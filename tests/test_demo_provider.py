import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.create_sample_db import create_sample_database
from src.database import SQLiteReadOnlyDatabase
from src.models import TextToSQLRequest
from src.providers.demo_provider import (
    DemoTextToSQLProvider,
    UNSUPPORTED_DEMO_QUESTION_MESSAGE,
)
from src.providers.models import GeneratedSQL, LLMResponseError
from src.rbac import UserContext, UserRole
from src.service import TextToSQLService


QUESTIONS_BY_ROLE = {
    UserRole.SALES_ANALYST: (
        "What are the top 5 products by total completed-order revenue?",
        "Show monthly completed-order revenue for 2025.",
        "Which regions have the highest number of completed orders?",
        "Which customers placed the most completed orders?",
    ),
    UserRole.SALES_MANAGER: (
        "Which employees manage the highest-revenue customer portfolios?",
        "Show customer count by account manager.",
        "Compare completed-order revenue by region.",
        "Which products generate the most revenue?",
    ),
    UserRole.ACCOUNT_MANAGER: (
        "Show my assigned customers.",
        "Which of my customers generated the most revenue?",
        "Show completed orders for my customers.",
        "What products were purchased most by my customers?",
    ),
}
SCHEMA = "Table: customers\nColumns:\n- customer_id (INTEGER)"


class TrackingDatabase(SQLiteReadOnlyDatabase):
    def __init__(self, database_path: Path) -> None:
        super().__init__(database_path)
        self.authorized_sql_calls: list[str] = []

    def execute_authorized(self, rbac_result, parameters: tuple = ()):
        self.authorized_sql_calls.append(rbac_result.sql)
        return super().execute_authorized(rbac_result, parameters)


@pytest.fixture()
def database(tmp_path: Path) -> TrackingDatabase:
    path = tmp_path / "company.db"
    create_sample_database(path)
    return TrackingDatabase(path)


def test_demo_provider_requires_no_key_or_client() -> None:
    provider = DemoTextToSQLProvider()

    assert provider.provider_name == "demo"
    assert provider.model_name == "secure-demo-generator"
    assert not hasattr(provider, "_client")


@pytest.mark.parametrize(
    "question",
    [question for questions in QUESTIONS_BY_ROLE.values() for question in questions],
)
def test_demo_provider_supports_every_documented_question(question: str) -> None:
    generated = DemoTextToSQLProvider().generate_sql(question, SCHEMA)

    assert isinstance(generated, GeneratedSQL)
    assert generated.provider == "demo"
    assert generated.model == "secure-demo-generator"
    assert generated.sql.lstrip().upper().startswith(("SELECT", "WITH"))
    assert generated.explanation


def test_demo_question_matching_normalizes_case_and_surrounding_whitespace() -> None:
    generated = DemoTextToSQLProvider().generate_sql(
        " \n  SHOW MY ASSIGNED CUSTOMERS. \t",
        SCHEMA,
    )

    assert "FROM customers" in generated.sql


def test_demo_provider_rejects_unsupported_questions_safely() -> None:
    with pytest.raises(LLMResponseError) as exc_info:
        DemoTextToSQLProvider().generate_sql("Delete all customers", SCHEMA)

    assert str(exc_info.value) == UNSUPPORTED_DEMO_QUESTION_MESSAGE


@pytest.mark.parametrize("question", QUESTIONS_BY_ROLE[UserRole.ACCOUNT_MANAGER])
def test_account_manager_sql_has_no_embedded_employee_filter(question: str) -> None:
    sql = DemoTextToSQLProvider().generate_sql(question, SCHEMA).sql.casefold()

    assert "account_manager_id" not in sql
    assert "employee_id" not in sql
    assert "__rbac_" not in sql


@pytest.mark.parametrize(
    ("role", "question"),
    [
        (role, question)
        for role, questions in QUESTIONS_BY_ROLE.items()
        for question in questions
    ],
)
def test_all_demo_queries_run_through_real_security_pipeline(
    database: TrackingDatabase,
    role: UserRole,
    question: str,
) -> None:
    employee_id = 1 if role == UserRole.ACCOUNT_MANAGER else None
    user = UserContext(user_id=f"demo-{role.value}", role=role, employee_id=employee_id)
    response = TextToSQLService(database, DemoTextToSQLProvider()).answer(
        TextToSQLRequest(question=question, user=user)
    )

    assert response.success is True, response.error_message
    assert response.guardrail_checks
    assert response.rbac_checks
    assert database.authorized_sql_calls[-1] == response.authorized_sql
    if role == UserRole.ACCOUNT_MANAGER:
        assert response.row_level_security_applied is True
        assert response.generated_sql != response.authorized_sql
        assert "__rbac_" in response.authorized_sql
    else:
        assert response.row_level_security_applied is False


def test_sales_manager_has_no_false_rls_notice(database: TrackingDatabase) -> None:
    response = TextToSQLService(database, DemoTextToSQLProvider()).answer(
        TextToSQLRequest(
            question=QUESTIONS_BY_ROLE[UserRole.SALES_MANAGER][0],
            user=UserContext(user_id="manager", role=UserRole.SALES_MANAGER),
        )
    )

    assert response.success is True
    assert response.row_level_security_applied is False
