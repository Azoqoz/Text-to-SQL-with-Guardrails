import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.create_sample_db import create_sample_database
from src.database import SQLiteReadOnlyDatabase
from src.guardrails import SQLGuardrails, UnsafeSQLError


ALLOWED_TABLES = {"customers", "employees", "order_items", "orders", "products"}


@pytest.fixture()
def guardrails() -> SQLGuardrails:
    return SQLGuardrails()


@pytest.fixture()
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "company.db"
    create_sample_database(path)
    return path


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT employee_id, employee_name FROM employees",
        """
        SELECT customers.customer_name, orders.order_id
        FROM customers
        JOIN orders ON orders.customer_id = customers.customer_id
        """,
        """
        SELECT customers.region, COUNT(*) AS customer_count
        FROM customers
        GROUP BY customers.region
        """,
        """
        WITH sales_employees AS (
            SELECT employee_id FROM employees WHERE department = 'Sales'
        )
        SELECT employee_id FROM sales_employees
        """,
        """
        SELECT customer_name
        FROM customers
        WHERE customer_id IN (
            SELECT customer_id FROM orders WHERE status = 'Completed'
        )
        """,
        "SELECT customer_id FROM customers UNION SELECT customer_id FROM orders",
        "SELECT customer_id FROM customers INTERSECT SELECT customer_id FROM orders",
        "SELECT customer_id FROM customers EXCEPT SELECT customer_id FROM orders",
        "select employee_id from employees",
        "SELECT employee_id FROM Employees",
    ],
)
def test_guardrails_accept_read_only_queries(guardrails: SQLGuardrails, sql: str) -> None:
    result = guardrails.validate(sql, ALLOWED_TABLES)

    assert result.normalized_sql
    assert not result.normalized_sql.endswith(";")
    assert result.referenced_tables
    assert result.checks


@pytest.mark.parametrize(
    "sql",
    [
        "",
        "SELECT * FROM employees -- comment",
        "SELECT * FROM employees /* comment */",
        "SELECT * FROM employees; SELECT * FROM customers",
        "INSERT INTO employees (employee_id) VALUES (99)",
        "UPDATE employees SET department = 'Sales'",
        "DELETE FROM employees",
        "CREATE TABLE test_table (id INTEGER)",
        "DROP TABLE employees",
        "ALTER TABLE employees ADD COLUMN test_column TEXT",
        "REPLACE INTO employees (employee_id) VALUES (99)",
        "ATTACH DATABASE 'other.db' AS other",
        "DETACH DATABASE other",
        "PRAGMA table_info(employees)",
        "VACUUM",
        "REINDEX",
        "ANALYZE",
        "SELECT * FROM invoices",
        "SELECT * FROM employees",
        """
        WITH hidden AS (
            SELECT employee_id FROM payroll
        )
        SELECT employee_id FROM hidden
        """,
    ],
)
def test_guardrails_reject_unsafe_queries(guardrails: SQLGuardrails, sql: str) -> None:
    allowed_tables = ALLOWED_TABLES - {"employees"} if sql == "SELECT * FROM employees" else ALLOWED_TABLES

    with pytest.raises(UnsafeSQLError):
        guardrails.validate(sql, allowed_tables)


def test_cte_aliases_are_not_rejected_as_unknown_tables(guardrails: SQLGuardrails) -> None:
    result = guardrails.validate(
        """
        WITH recent_orders AS (
            SELECT order_id, customer_id FROM orders
        )
        SELECT customers.customer_name
        FROM recent_orders
        JOIN customers ON customers.customer_id = recent_orders.customer_id
        """,
        ALLOWED_TABLES,
    )

    assert result.referenced_tables == {"orders", "customers"}


def test_referenced_tables_contains_only_physical_tables(guardrails: SQLGuardrails) -> None:
    result = guardrails.validate(
        """
        WITH sales_employees AS (
            SELECT employee_id FROM employees
        )
        SELECT employee_id FROM sales_employees
        """,
        ALLOWED_TABLES,
    )

    assert result.referenced_tables == {"employees"}
    assert "sales_employees" not in result.referenced_tables


def test_normalized_sql_is_returned_without_trailing_semicolon(guardrails: SQLGuardrails) -> None:
    result = guardrails.validate("select employee_id from employees;", ALLOWED_TABLES)

    assert result.normalized_sql == "SELECT employee_id FROM employees"
    assert not result.normalized_sql.endswith(";")


def test_execute_validated_runs_accepted_query(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)
    validation = SQLGuardrails().validate(
        "SELECT employee_id, employee_name FROM employees ORDER BY employee_id",
        set(database.get_table_names()),
    )

    result = database.execute_validated(validation)

    assert result.row_count == 4
    assert result.truncated is False
    assert result.rows[0] == {"employee_id": 1, "employee_name": "Amina Saleh"}


def test_execute_validated_keeps_result_limiting_and_truncation(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path, max_result_rows=5)
    validation = SQLGuardrails().validate(
        "SELECT order_id FROM orders ORDER BY order_id",
        set(database.get_table_names()),
    )

    result = database.execute_validated(validation)

    assert result.row_count == 5
    assert len(result.rows) == 5
    assert result.truncated is True
