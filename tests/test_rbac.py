import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.create_sample_db import create_sample_database
from src.database import SQLiteReadOnlyDatabase
from src.rbac import (
    AccessDeniedError,
    RBACEnforcer,
    UserContext,
    UserRole,
    apply_row_level_security,
    get_allowed_columns,
    get_allowed_tables,
    get_role_policy,
    validate_column_access,
)


@pytest.fixture()
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "company.db"
    create_sample_database(path)
    return path


@pytest.fixture()
def database(database_path: Path) -> SQLiteReadOnlyDatabase:
    return SQLiteReadOnlyDatabase(database_path)


@pytest.fixture()
def physical_schema(database: SQLiteReadOnlyDatabase) -> dict[str, set[str]]:
    return database.get_physical_schema()


def test_user_context_validation() -> None:
    with pytest.raises(ValueError):
        UserContext(user_id="u1", role=UserRole.ACCOUNT_MANAGER)

    assert UserContext(user_id="u2", role=UserRole.SALES_ANALYST).employee_id is None
    assert UserContext(user_id="u3", role=UserRole.SALES_MANAGER).employee_id is None


def test_role_policies() -> None:
    analyst = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)
    manager = UserContext(user_id="manager", role=UserRole.SALES_MANAGER)
    account_manager = UserContext(
        user_id="account-manager",
        role=UserRole.ACCOUNT_MANAGER,
        employee_id=1,
    )

    assert "employees" not in get_allowed_tables(analyst)
    assert "employees" in get_allowed_tables(manager)
    assert get_role_policy(account_manager).row_filters


def test_schema_filtering(database: SQLiteReadOnlyDatabase) -> None:
    analyst_schema = database.get_schema_for_user(
        UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)
    )
    manager_schema = database.get_schema_for_user(
        UserContext(user_id="manager", role=UserRole.SALES_MANAGER)
    )
    account_manager_schema = database.get_schema_for_user(
        UserContext(user_id="account-manager", role=UserRole.ACCOUNT_MANAGER, employee_id=1)
    )

    assert "Table: employees" not in analyst_schema
    assert "account_manager_id" not in analyst_schema
    assert "Table: employees" in manager_schema
    assert "account_manager_id" in manager_schema
    assert "account_manager_id" not in account_manager_schema


def test_table_level_access(physical_schema: dict[str, set[str]]) -> None:
    enforcer = RBACEnforcer()

    with pytest.raises(AccessDeniedError):
        enforcer.enforce(
            "SELECT employee_name FROM employees",
            UserContext(user_id="analyst", role=UserRole.SALES_ANALYST),
            physical_schema,
        )

    manager_result = enforcer.enforce(
        "SELECT employee_name FROM employees",
        UserContext(user_id="manager", role=UserRole.SALES_MANAGER),
        physical_schema,
    )
    assert manager_result.role == UserRole.SALES_MANAGER

    with pytest.raises(AccessDeniedError):
        enforcer.enforce(
            "SELECT employee_name FROM employees",
            UserContext(user_id="account-manager", role=UserRole.ACCOUNT_MANAGER, employee_id=1),
            physical_schema,
        )


def test_column_level_access_basic(physical_schema: dict[str, set[str]]) -> None:
    analyst = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)
    manager = UserContext(user_id="manager", role=UserRole.SALES_MANAGER)

    validate_column_access("SELECT customers.customer_name FROM customers", analyst, physical_schema)

    with pytest.raises(AccessDeniedError):
        validate_column_access("SELECT customers.account_manager_id FROM customers", analyst, physical_schema)

    with pytest.raises(AccessDeniedError):
        validate_column_access("SELECT * FROM customers", analyst, physical_schema)

    validate_column_access("SELECT * FROM customers", manager, physical_schema)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT customer_name FROM customers WHERE account_manager_id = 1",
        """
        SELECT c.customer_name
        FROM customers c
        JOIN orders o ON c.account_manager_id = o.customer_id
        """,
        "SELECT region, COUNT(*) FROM customers GROUP BY account_manager_id",
        "SELECT customer_name FROM customers ORDER BY account_manager_id",
        """
        SELECT region, COUNT(*) AS total
        FROM customers
        GROUP BY region
        HAVING account_manager_id = 1
        """,
        """
        SELECT customer_name
        FROM customers
        WHERE customer_id IN (
            SELECT customer_id FROM customers WHERE account_manager_id = 1
        )
        """,
    ],
)
def test_unauthorized_columns_in_query_clauses_are_rejected(
    physical_schema: dict[str, set[str]],
    sql: str,
) -> None:
    analyst = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)

    with pytest.raises(AccessDeniedError):
        validate_column_access(sql, analyst, physical_schema)


def test_aliases_and_ambiguous_columns(physical_schema: dict[str, set[str]]) -> None:
    analyst = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)

    validate_column_access("SELECT c.customer_name FROM customers AS c", analyst, physical_schema)

    with pytest.raises(AccessDeniedError):
        validate_column_access(
            """
            SELECT customer_id
            FROM customers
            JOIN orders ON orders.customer_id = customers.customer_id
            """,
            analyst,
            physical_schema,
        )


def test_cte_aliases_do_not_cause_false_column_failures(
    physical_schema: dict[str, set[str]],
) -> None:
    analyst = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)

    validate_column_access(
        """
        WITH customer_names AS (
            SELECT customer_id, customer_name FROM customers
        )
        SELECT customer_names.customer_name FROM customer_names
        """,
        analyst,
        physical_schema,
    )


def test_account_manager_selecting_customers_receives_only_assigned_customers(
    database: SQLiteReadOnlyDatabase,
    database_path: Path,
) -> None:
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)
    result = _execute_authorized(
        database,
        "SELECT customer_id, customer_name FROM customers ORDER BY customer_id",
        user,
    )
    expected_ids = _assigned_customer_ids(database_path, employee_id=1)

    assert {row["customer_id"] for row in result.rows} == expected_ids


def test_account_manager_selecting_orders_receives_only_assigned_customer_orders(
    database: SQLiteReadOnlyDatabase,
    database_path: Path,
) -> None:
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)
    result = _execute_authorized(
        database,
        "SELECT order_id, customer_id FROM orders ORDER BY order_id",
        user,
    )
    expected_customer_ids = _assigned_customer_ids(database_path, employee_id=1)

    assert result.rows
    assert {row["customer_id"] for row in result.rows}.issubset(expected_customer_ids)


def test_account_manager_selecting_order_items_receives_only_assigned_customer_items(
    database: SQLiteReadOnlyDatabase,
    database_path: Path,
) -> None:
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)
    result = _execute_authorized(
        database,
        """
        SELECT order_items.order_item_id, order_items.order_id
        FROM order_items
        ORDER BY order_items.order_item_id
        """,
        user,
    )
    expected_order_ids = _assigned_order_ids(database_path, employee_id=1)

    assert result.rows
    assert {row["order_id"] for row in result.rows}.issubset(expected_order_ids)


def test_account_manager_cannot_bypass_filter_with_where_clause(
    database: SQLiteReadOnlyDatabase,
    database_path: Path,
) -> None:
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)
    other_customer_id = _customer_id_for_other_manager(database_path, employee_id=1)

    result = _execute_authorized(
        database,
        f"SELECT customer_id FROM customers WHERE customer_id = {other_customer_id}",
        user,
    )

    assert result.rows == []


def test_account_manager_joins_remain_filtered(
    database: SQLiteReadOnlyDatabase,
    database_path: Path,
) -> None:
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)
    result = _execute_authorized(
        database,
        """
        SELECT customers.customer_id, orders.order_id
        FROM customers
        JOIN orders ON orders.customer_id = customers.customer_id
        ORDER BY orders.order_id
        """,
        user,
    )
    expected_customer_ids = _assigned_customer_ids(database_path, employee_id=1)

    assert result.rows
    assert {row["customer_id"] for row in result.rows}.issubset(expected_customer_ids)


def test_sales_roles_receive_rows_across_multiple_account_managers(
    database: SQLiteReadOnlyDatabase,
    database_path: Path,
) -> None:
    analyst = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)
    manager = UserContext(user_id="manager", role=UserRole.SALES_MANAGER)

    analyst_result = _execute_authorized(
        database,
        "SELECT customer_id, customer_name FROM customers ORDER BY customer_id",
        analyst,
    )
    manager_result = _execute_authorized(
        database,
        "SELECT customer_id, account_manager_id FROM customers ORDER BY customer_id",
        manager,
    )

    assert _account_manager_count_for_customer_rows(database_path, analyst_result.rows) > 1
    assert len({row["account_manager_id"] for row in manager_result.rows}) > 1


def test_product_only_query_is_not_unnecessarily_restricted(database: SQLiteReadOnlyDatabase) -> None:
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)

    result = _execute_authorized(
        database,
        "SELECT product_id, product_name FROM products ORDER BY product_id",
        user,
    )

    assert result.row_count == 8


def test_full_flow_returns_authorized_query_result(database: SQLiteReadOnlyDatabase) -> None:
    user = UserContext(user_id="am1", role=UserRole.ACCOUNT_MANAGER, employee_id=1)
    enforcer = RBACEnforcer()

    rbac_result = enforcer.enforce(
        "SELECT customer_id, customer_name FROM customers ORDER BY customer_id",
        user,
        database.get_physical_schema(),
    )
    result = database.execute_authorized(rbac_result)

    assert rbac_result.row_level_security_applied is True
    assert result.row_count > 0
    assert result.truncated is False


def test_apply_row_level_security_returns_normalized_sql_for_unrestricted_roles() -> None:
    user = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)

    assert apply_row_level_security("select product_id from products;", user) == "SELECT product_id FROM products"


def test_allowed_columns_helper_returns_mutable_sets() -> None:
    user = UserContext(user_id="analyst", role=UserRole.SALES_ANALYST)

    allowed_columns = get_allowed_columns(user)

    assert allowed_columns["customers"] == {"customer_id", "customer_name", "region", "signup_date"}


def _execute_authorized(
    database: SQLiteReadOnlyDatabase,
    sql: str,
    user: UserContext,
):
    rbac_result = RBACEnforcer().enforce(sql, user, database.get_physical_schema())
    return database.execute_authorized(rbac_result)


def _assigned_customer_ids(database_path: Path, employee_id: int) -> set[int]:
    with sqlite3.connect(database_path) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT customer_id FROM customers WHERE account_manager_id = ?",
                (employee_id,),
            )
        }


def _assigned_order_ids(database_path: Path, employee_id: int) -> set[int]:
    with sqlite3.connect(database_path) as connection:
        return {
            row[0]
            for row in connection.execute(
                """
                SELECT orders.order_id
                FROM orders
                JOIN customers ON customers.customer_id = orders.customer_id
                WHERE customers.account_manager_id = ?
                """,
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


def _account_manager_count_for_customer_rows(
    database_path: Path,
    rows: list[dict[str, object]],
) -> int:
    customer_ids = [int(row["customer_id"]) for row in rows]
    placeholders = ",".join("?" for _ in customer_ids)
    with sqlite3.connect(database_path) as connection:
        result = connection.execute(
            f"""
            SELECT COUNT(DISTINCT account_manager_id)
            FROM customers
            WHERE customer_id IN ({placeholders})
            """,
            customer_ids,
        ).fetchone()
    return int(result[0])
