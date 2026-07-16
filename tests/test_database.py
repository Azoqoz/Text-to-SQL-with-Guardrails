import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.create_sample_db import create_sample_database
from src.database import SQLiteReadOnlyDatabase


EXPECTED_TABLES = {"customers", "employees", "order_items", "orders", "products"}
REJECTED_STATEMENTS = [
    "INSERT INTO employees (employee_id, employee_name, department, hire_date) VALUES (99, 'A', 'Sales', '2025-01-01')",
    "UPDATE employees SET department = 'Sales'",
    "DELETE FROM employees",
    "DROP TABLE employees",
    "PRAGMA table_info(employees)",
    "ATTACH DATABASE 'other.db' AS other",
]


@pytest.fixture()
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "company.db"
    create_sample_database(path)
    return path


def test_constructor_rejects_missing_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        SQLiteReadOnlyDatabase(tmp_path / "missing.db")


@pytest.mark.parametrize("max_result_rows", [0, -1, 10_001])
def test_constructor_rejects_invalid_max_result_rows(
    database_path: Path,
    max_result_rows: int,
) -> None:
    with pytest.raises(ValueError):
        SQLiteReadOnlyDatabase(database_path, max_result_rows=max_result_rows)


def test_all_table_names_are_returned(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    assert set(database.get_table_names()) == EXPECTED_TABLES
    assert database.get_table_names() == sorted(EXPECTED_TABLES)


def test_schema_description_contains_tables_and_foreign_keys(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    schema = database.get_schema()

    for table_name in EXPECTED_TABLES:
        assert f"Table: {table_name}" in schema
    assert "customer_id -> customers.customer_id" in schema


def test_safe_select_returns_rows_as_dictionaries(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    result = database.execute_select(
        "SELECT employee_id, employee_name FROM employees WHERE department = ?",
        ("Sales",),
    )

    assert result.row_count == 2
    assert result.truncated is False
    assert isinstance(result.rows[0], dict)
    assert set(result.rows[0]) == {"employee_id", "employee_name"}


def test_with_query_is_accepted(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    result = database.execute_select(
        """
        WITH sales_employees AS (
            SELECT employee_id FROM employees WHERE department = 'Sales'
        )
        SELECT COUNT(*) AS employee_count FROM sales_employees
        """
    )

    assert result.rows == [{"employee_count": 2}]


def test_empty_sql_is_rejected(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    with pytest.raises(ValueError):
        database.execute_select("   ")


@pytest.mark.parametrize("sql", REJECTED_STATEMENTS)
def test_non_select_statements_are_rejected(database_path: Path, sql: str) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    with pytest.raises(ValueError):
        database.execute_select(sql)


def test_multiple_statements_are_rejected(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    with pytest.raises(ValueError):
        database.execute_select("SELECT * FROM employees; SELECT * FROM customers")


def test_write_attempt_cannot_succeed_through_read_only_connection(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path)

    with database._connect_read_only() as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute(
                """
                INSERT INTO employees (employee_id, employee_name, department, hire_date)
                VALUES (99, 'Test User', 'Sales', '2025-01-01')
                """
            )


def test_result_limiting_and_truncation(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path, max_result_rows=3)

    result = database.execute_select("SELECT order_id FROM orders ORDER BY order_id")

    assert result.row_count == 3
    assert len(result.rows) == 3
    assert result.truncated is True


def test_truncated_is_false_when_all_rows_fit(database_path: Path) -> None:
    database = SQLiteReadOnlyDatabase(database_path, max_result_rows=10)

    result = database.execute_select("SELECT employee_id FROM employees ORDER BY employee_id")

    assert result.row_count == 4
    assert len(result.rows) == 4
    assert result.truncated is False
