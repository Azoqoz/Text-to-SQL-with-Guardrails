from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from src.api import create_app
from src.application import EXAMPLE_QUESTIONS, ProviderOptions, TextToSQLApplication
from src.config import ApplicationMode
from src.providers.factory import ProviderType
from src.rbac import RBACEnforcer, UserContext, UserRole, get_allowed_tables


MUTATION = "Delete all customers from the database."
EMPLOYEES = "Show all employees and their details."
CASES = [
    (UserRole.SALES_ANALYST, MUTATION, "DELETE FROM customers", "unsafe_sql"),
    (UserRole.SALES_ANALYST, EMPLOYEES, "SELECT employee_id, employee_name, department, hire_date FROM employees", "access_denied"),
    (UserRole.SALES_MANAGER, MUTATION, "DELETE FROM customers", "unsafe_sql"),
    (UserRole.ACCOUNT_MANAGER, MUTATION, "DELETE FROM customers", "unsafe_sql"),
    (UserRole.ACCOUNT_MANAGER, EMPLOYEES, "SELECT employee_id, employee_name, department, hire_date FROM employees", "access_denied"),
]


@pytest.mark.parametrize("role, question, sql, error_type", CASES)
def test_library_examples_reach_real_backend_guards_without_execution(
    settings, database, monkeypatch, role, question, sql, error_type,
):
    enforce = RBACEnforcer.enforce
    attempts = []

    def track_enforcement(self, **kwargs):
        attempts.append(kwargs)
        return enforce(self, **kwargs)

    monkeypatch.setattr(RBACEnforcer, "enforce", track_enforcement)
    factory = Mock(side_effect=AssertionError("Demo must stay offline"))
    service = TextToSQLApplication(
        settings, database_factory=lambda **kwargs: database, provider_factory=factory,
    )
    employee_id = 1 if role == UserRole.ACCOUNT_MANAGER else None
    user = UserContext("visitor", role, employee_id)
    before = settings.database_path.read_bytes()
    with TestClient(create_app(service=service)) as client:
        params = {"role": role.value}
        if employee_id is not None:
            params["employee_id"] = employee_id
        metadata = client.get("/capabilities", params=params)
        assert metadata.status_code == 200
        selected = next(r for r in metadata.json()["roles"] if r["role"] == role.value)
        expected_tests = [MUTATION] if role == UserRole.SALES_MANAGER else [MUTATION, EMPLOYEES]
        assert selected["guardrail_test_questions"] == expected_tests
        assert selected["example_questions"] == list(EXAMPLE_QUESTIONS[role]) + expected_tests
        if error_type == "access_denied":
            assert "Table: employees" not in metadata.json()["schema"]
            assert "employees" in database.get_physical_schema()
            assert "employees" not in get_allowed_tables(user)
        response = client.post("/query", json={
            "question": question, "user_id": user.user_id, "role": role.value,
            "employee_id": employee_id,
        })
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["error_type"] == error_type
    assert payload["error_message"] == (
        "Generated SQL did not pass safety checks." if error_type == "unsafe_sql"
        else "The query is not allowed for this user role."
    )
    assert payload["provider"] == "demo"
    assert payload["rows"] == [] and payload["row_count"] == 0
    assert payload["generated_sql"] == payload["authorized_sql"] == ""
    assert len(attempts) == 1
    assert attempts[0]["sql"] == sql
    assert attempts[0]["user"] == user
    database.execute_authorized.assert_not_called()
    factory.assert_not_called()
    assert settings.database_path.read_bytes() == before


def test_sales_manager_has_no_artificial_restricted_table_example(settings, database):
    user = UserContext("manager", UserRole.SALES_MANAGER)
    assert set(database.get_physical_schema()) <= get_allowed_tables(user)
    response = TextToSQLApplication(settings).query(EMPLOYEES, user)
    assert response.error_type == "validation_error"  # Outside this role's demo catalog.


@pytest.mark.parametrize("role", list(UserRole))
def test_local_library_and_original_demo_provider_are_unchanged(settings, role):
    service = TextToSQLApplication(replace(settings, app_mode=ApplicationMode.LOCAL_FULL))
    user = UserContext("local", role, 1 if role == UserRole.ACCOUNT_MANAGER else None)
    capability = next(r for r in service.capabilities(user).roles if r.role == role)
    assert capability.example_questions == EXAMPLE_QUESTIONS[role]
    assert capability.guardrail_test_questions == ()
    for question in (MUTATION, EMPLOYEES):
        response = service.query(question, user)
        assert response.error_type == "invalid_provider_response"


@pytest.mark.parametrize("question", [MUTATION, EMPLOYEES])
def test_local_requests_still_use_selected_provider(settings, database, provider, question):
    factory = Mock(return_value=provider)
    service = TextToSQLApplication(
        replace(settings, app_mode=ApplicationMode.LOCAL_FULL),
        database_factory=lambda **kwargs: database, provider_factory=factory,
    )
    user = UserContext("local", UserRole.SALES_ANALYST)
    response = service.query(question, user, ProviderOptions(provider=ProviderType.OPENAI))
    assert response.success  # The injected local adapter supplies its ordinary SELECT.
    factory.assert_called_once()
    assert factory.call_args.kwargs["provider_type"] == ProviderType.OPENAI
    provider.generate_sql.assert_called_once_with(question, database.get_schema_for_user(user))
    database.execute_authorized.assert_called_once()
