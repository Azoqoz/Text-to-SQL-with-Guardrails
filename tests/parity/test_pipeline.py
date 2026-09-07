"""Observable pre-migration contracts, including current validation quirks."""

import sqlite3

import pytest

from src.guardrails import SQLGuardrails, UnsafeSQLError
from src.rbac import UserRole


@pytest.mark.parametrize("sql, expected", [
    ("select customer_id from customers order by customer_id;", [1, 2, 3]),
    ("WITH chosen AS (SELECT customer_id FROM customers WHERE region = 'East') "
     "SELECT customer_id FROM chosen ORDER BY customer_id", [1, 3]),
    ("SELECT customer_id FROM customers WHERE customer_id = 1 UNION "
     "SELECT customer_id FROM customers WHERE customer_id = 3 ORDER BY customer_id", [1, 3]),
    ("SELECT customers.customer_id FROM customers INTERSECT "
     "SELECT orders.customer_id FROM orders ORDER BY customers.customer_id", [1, 2, 3]),
    ("SELECT customers.customer_id FROM customers EXCEPT SELECT orders.customer_id FROM orders", []),
])
def test_read_only_query_forms_execute(run_sql, database, sql, expected):
    response, provider = run_sql(sql)
    assert response.success, response.error_message
    assert response.rows == [{"customer_id": value} for value in expected]
    assert response.row_count == len(expected)
    assert response.truncated is False
    assert response.generated_sql == sql
    assert not response.authorized_sql.endswith(";")
    assert (response.provider, response.model, response.explanation) == ("parity", "fixed-sql", "Parity query.")
    assert response.question == "List data"
    assert response.role == UserRole.SALES_ANALYST
    assert response.error_type is response.error_message is None
    assert response.row_level_security_applied is False
    assert response.guardrail_checks.count("SQL parsed successfully with SQLGlot SQLite dialect") == 2
    assert "Transformed SQL revalidated by SQL guardrails" in response.rbac_checks
    provider.generate_sql.assert_called_once()
    database.execute_authorized.assert_called_once()
    assert database.execute_authorized.call_args.args[0].sql == response.authorized_sql


@pytest.mark.parametrize("role", list(UserRole))
def test_schema_sent_to_provider_filters_columns_tables_and_foreign_keys(run_sql, role):
    response, provider = run_sql("SELECT product_id FROM products", role, 1)
    assert response.success
    question, schema = provider.generate_sql.call_args.args
    assert question == "List data"
    tables = {line.removeprefix("Table: ") for line in schema.splitlines() if line.startswith("Table: ")}
    expected = {"customers", "orders", "order_items", "products"}
    if role == UserRole.SALES_MANAGER:
        expected.add("employees")
        assert "account_manager_id -> employees.employee_id" in schema
    else:
        assert "account_manager_id" not in schema
        assert "employees" not in schema
    assert tables == expected
    assert "salary" not in schema
    assert "customer_id -> customers.customer_id" in schema
    assert "order_id -> orders.order_id" in schema
    assert "Alpha" not in schema  # Schema metadata, never row values.


@pytest.mark.parametrize("sql", [
    "DELETE FROM customers", "UPDATE customers SET region = 'West'",
    "INSERT INTO customers (customer_id) VALUES (99)", "DROP TABLE customers",
    "CREATE TABLE stolen (id INTEGER)", "PRAGMA table_info(customers)",
    "ATTACH DATABASE 'other.db' AS other", "VALUES (1)",
    "SELECT customer_id FROM customers; SELECT product_id FROM products",
    "SELECT customer_id FROM customers -- comment",
    "SELECT customer_id FROM customers /* comment */",
    "SELECT ( FROM customers",  # Real SQLGlot parse failure.
    "WITH hidden AS (DELETE FROM customers RETURNING customer_id) SELECT * FROM hidden",
    "SELECT 'contains--comment-marker'",  # Current lexical rejection also applies to literals.
])
def test_unsafe_sql_never_reaches_execution(run_sql, database, sql):
    before = database.database_path.read_bytes()
    response, _ = run_sql(sql)
    assert not response.success
    assert response.error_type == "unsafe_sql"
    assert response.error_message == "Generated SQL did not pass safety checks."
    assert response.generated_sql == response.authorized_sql == response.explanation == ""
    assert response.rows == [] and response.row_count == 0
    assert response.truncated is response.row_level_security_applied is False
    assert response.guardrail_checks == response.rbac_checks == []
    database.execute_authorized.assert_not_called()
    assert database.database_path.read_bytes() == before


@pytest.mark.parametrize("sql", [
    "SELECT employee_name FROM employees",
    "SELECT salary FROM payroll",
    "SELECT name FROM sqlite_master",
    "SELECT customer_id FROM missing_table",
    "WITH hidden AS (SELECT salary FROM payroll) SELECT salary FROM hidden",
    "SELECT product_id FROM products UNION SELECT employee_id FROM payroll",
    "SELECT account_manager_id FROM customers",
    "SELECT customer_id FROM customers WHERE account_manager_id = 1",
    "SELECT customer_id FROM customers ORDER BY account_manager_id",
    "SELECT * FROM customers",
    "SELECT c.* FROM customers AS c",
    "SELECT COUNT(*) FROM customers",  # Current star policy also rejects COUNT(*).
    # Current ambiguity checks span both branches, even though SQLite scopes them separately.
    "SELECT customer_id FROM customers INTERSECT SELECT customer_id FROM orders",
    "SELECT customer_id FROM customers EXCEPT SELECT customer_id FROM orders",
])
def test_allowlist_and_column_failures_have_access_denied_contract(run_sql, database, sql):
    response, _ = run_sql(sql)
    assert not response.success
    assert response.error_type == "access_denied"
    assert response.error_message == "The query is not allowed for this user role."
    assert response.rows == []
    database.execute_authorized.assert_not_called()


def test_manager_access_still_excludes_non_policy_tables(run_sql, database):
    response, _ = run_sql("SELECT employee_name FROM employees ORDER BY employee_id", UserRole.SALES_MANAGER)
    assert response.success
    assert response.rows == [{"employee_name": "Amina"}, {"employee_name": "Ben"}]
    database.execute_authorized.reset_mock()
    response, _ = run_sql("SELECT salary FROM payroll", UserRole.SALES_MANAGER)
    assert response.error_type == "access_denied"
    database.execute_authorized.assert_not_called()


@pytest.mark.parametrize("employee_id, customer_ids, order_ids, item_ids, revenue", [
    (1, [1, 3], [11, 13], [101, 103], 40.0),
    (2, [2], [12], [102], 90.0),
    (99, [], [], [], None),
])
def test_row_filters_cover_each_relation_and_joined_aggregate(
    run_sql, database, employee_id, customer_ids, order_ids, item_ids, revenue,
):
    for table, column, expected in [
        ("customers", "customer_id", customer_ids),
        ("orders", "order_id", order_ids),
        ("order_items", "order_item_id", item_ids),
    ]:
        response, _ = run_sql(f"SELECT {column} FROM {table} ORDER BY {column}", UserRole.ACCOUNT_MANAGER, employee_id)
        assert response.success, response.error_message
        assert response.rows == [{column: value} for value in expected]
        assert response.row_level_security_applied is True
        assert response.generated_sql != response.authorized_sql
        assert database.execute_authorized.call_args.args[0].sql == response.authorized_sql
    response, _ = run_sql(
        "SELECT SUM(i.quantity * i.unit_price) AS revenue FROM customers c "
        "JOIN orders o ON o.customer_id = c.customer_id JOIN order_items i ON i.order_id = o.order_id",
        UserRole.ACCOUNT_MANAGER, employee_id,
    )
    assert response.success, response.error_message
    assert response.rows == [{"revenue": revenue}]


@pytest.mark.parametrize("sql, expected", [
    ("SELECT customer_id FROM customers WHERE customer_id = 2", []),
    ("SELECT customer_id FROM customers WHERE customer_id = 2 OR 1 = 1 ORDER BY customer_id", [1, 3]),
    ("WITH chosen AS (SELECT customer_id FROM customers) SELECT customer_id FROM chosen ORDER BY customer_id", [1, 3]),
    ("SELECT customer_id FROM customers UNION SELECT customer_id FROM customers ORDER BY customer_id", [1, 3]),
])
def test_row_filters_survive_predicates_ctes_and_set_operations(run_sql, sql, expected):
    response, _ = run_sql(sql, UserRole.ACCOUNT_MANAGER, 1)
    assert response.success, response.error_message
    assert response.rows == [{"customer_id": value} for value in expected]
    assert response.row_level_security_applied


def test_row_restricted_table_cannot_be_shadowed(run_sql, database):
    response, _ = run_sql(
        "WITH customers AS (SELECT customer_id FROM orders) SELECT customer_id FROM customers",
        UserRole.ACCOUNT_MANAGER, 1,
    )
    assert response.error_type == "access_denied"
    database.execute_authorized.assert_not_called()


def test_products_remain_global_for_account_managers(run_sql):
    response, _ = run_sql("SELECT product_id FROM products ORDER BY product_id", UserRole.ACCOUNT_MANAGER, 99)
    assert response.success
    assert response.rows == [{"product_id": 1}, {"product_id": 2}]
    assert response.row_level_security_applied is False


@pytest.mark.parametrize("limit, clause, expected, truncated", [
    (2, "", [1, 2], True), (3, "", [1, 2, 3], False), (4, "", [1, 2, 3], False),
    (2, " LIMIT 1", [1], False), (2, " LIMIT 2", [1, 2], False),
    (2, " LIMIT 99", [1, 2], True), (2, " LIMIT 0", [], False),
])
def test_result_cap_and_sql_limit_contract(run_sql, database, limit, clause, expected, truncated):
    database.max_result_rows = limit
    response, _ = run_sql("SELECT customer_id FROM customers ORDER BY customer_id" + clause)
    assert response.success
    assert response.rows == [{"customer_id": value} for value in expected]
    assert response.row_count == len(expected)
    assert response.truncated is truncated
    assert response.authorized_sql == response.generated_sql  # Fetch cap does not rewrite SQL.


def test_row_filter_precedes_result_cap(run_sql, database):
    database.max_result_rows = 2
    response, _ = run_sql("SELECT customer_id FROM customers ORDER BY customer_id", UserRole.ACCOUNT_MANAGER, 1)
    assert response.success
    assert response.rows == [{"customer_id": 1}, {"customer_id": 3}]
    assert response.row_count == 2 and response.truncated is False


def test_sqlglot_normalization_and_physical_allowlist():
    result = SQLGuardrails().validate(
        "with chosen as (select product_id from Products) select product_id from chosen;", {"products"},
    )
    assert result.normalized_sql == "WITH chosen AS (SELECT product_id FROM Products) SELECT product_id FROM chosen"
    assert result.referenced_tables == {"products"}
    with pytest.raises(UnsafeSQLError, match="could not be parsed"):
        SQLGuardrails().validate("SELECT ( FROM products", {"products"})


def test_sqlite_read_only_defense_blocks_with_prefixed_write(database):
    before = database.database_path.read_bytes()
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        database.execute_select("WITH chosen AS (SELECT 1) DELETE FROM customers")
    assert database.database_path.read_bytes() == before


def test_unknown_unqualified_column_currently_maps_to_database_error(run_sql):
    response, _ = run_sql("SELECT nonexistent_column FROM products")
    assert not response.success
    assert response.error_type == "database_error"
    assert response.error_message == "The database could not execute the authorized query."
    assert response.rows == []
