from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum

import sqlglot
from sqlglot import exp

from src.guardrails import SQLGuardrails, UnsafeSQLError
from src.schema import build_schema_description


class AccessDeniedError(PermissionError):
    pass


class UserRole(str, Enum):
    SALES_ANALYST = "sales_analyst"
    SALES_MANAGER = "sales_manager"
    ACCOUNT_MANAGER = "account_manager"


@dataclass(frozen=True)
class UserContext:
    user_id: str
    role: UserRole
    employee_id: int | None = None

    def __post_init__(self) -> None:
        if self.role == UserRole.ACCOUNT_MANAGER and self.employee_id is None:
            raise ValueError("ACCOUNT_MANAGER users must include employee_id")
        if self.employee_id is not None and self.employee_id < 1:
            raise ValueError("employee_id must be a positive integer when provided")


@dataclass(frozen=True)
class RolePolicy:
    allowed_tables: frozenset[str]
    allowed_columns: dict[str, frozenset[str]]
    row_filters: dict[str, str]


@dataclass(frozen=True)
class RBACValidationResult:
    sql: str
    role: UserRole
    allowed_tables: set[str]
    row_level_security_applied: bool
    checks: list[str]
    guardrail_checks: list[str]
    rbac_checks: list[str]


SALES_ANALYST_COLUMNS: dict[str, frozenset[str]] = {
    "customers": frozenset({"customer_id", "customer_name", "region", "signup_date"}),
    "orders": frozenset({"order_id", "customer_id", "order_date", "status"}),
    "order_items": frozenset({"order_item_id", "order_id", "product_id", "quantity", "unit_price"}),
    "products": frozenset({"product_id", "product_name", "category", "unit_price"}),
}

SALES_MANAGER_COLUMNS: dict[str, frozenset[str]] = {
    "employees": frozenset({"employee_id", "employee_name", "department", "hire_date"}),
    "customers": frozenset(
        {"customer_id", "customer_name", "region", "signup_date", "account_manager_id"}
    ),
    "orders": frozenset({"order_id", "customer_id", "order_date", "status"}),
    "order_items": frozenset({"order_item_id", "order_id", "product_id", "quantity", "unit_price"}),
    "products": frozenset({"product_id", "product_name", "category", "unit_price"}),
}

ROLE_POLICIES: dict[UserRole, RolePolicy] = {
    UserRole.SALES_ANALYST: RolePolicy(
        allowed_tables=frozenset(SALES_ANALYST_COLUMNS),
        allowed_columns=SALES_ANALYST_COLUMNS,
        row_filters={},
    ),
    UserRole.SALES_MANAGER: RolePolicy(
        allowed_tables=frozenset(SALES_MANAGER_COLUMNS),
        allowed_columns=SALES_MANAGER_COLUMNS,
        row_filters={},
    ),
    UserRole.ACCOUNT_MANAGER: RolePolicy(
        allowed_tables=frozenset(SALES_ANALYST_COLUMNS),
        allowed_columns=SALES_ANALYST_COLUMNS,
        row_filters={
            "customers": "customers.account_manager_id = employee_id",
            "orders": "orders restricted to assigned customers",
            "order_items": "order_items restricted to assigned customer orders",
        },
    ),
}

RESTRICTED_TABLE_CTES = {
    "customers": "__rbac_customers",
    "orders": "__rbac_orders",
    "order_items": "__rbac_order_items",
}


def get_role_policy(user: UserContext) -> RolePolicy:
    return ROLE_POLICIES[user.role]


def get_allowed_tables(user: UserContext) -> set[str]:
    return set(get_role_policy(user).allowed_tables)


def get_allowed_columns(user: UserContext) -> dict[str, set[str]]:
    return {
        table: set(columns)
        for table, columns in get_role_policy(user).allowed_columns.items()
    }


def build_allowed_schema(connection: sqlite3.Connection, user: UserContext) -> str:
    return build_schema_description(
        connection,
        allowed_tables=get_allowed_tables(user),
        allowed_columns=get_allowed_columns(user),
    )


def validate_column_access(
    sql: str,
    user: UserContext,
    physical_schema: dict[str, set[str]],
) -> None:
    statement = _parse_one(sql)
    policy = get_role_policy(user)
    allowed_columns = {
        table.lower(): {column.lower() for column in columns}
        for table, columns in policy.allowed_columns.items()
    }
    normalized_schema = _normalize_schema(physical_schema)
    cte_aliases = _collect_cte_aliases(statement)
    alias_to_table = _collect_table_aliases(statement, cte_aliases, normalized_schema)
    physical_tables = set(alias_to_table.values())

    for star in statement.find_all(exp.Star):
        _validate_star_access(star, alias_to_table, physical_tables, allowed_columns, normalized_schema)

    for column in statement.find_all(exp.Column):
        if column.name == "*":
            continue
        _validate_column(
            column,
            alias_to_table,
            physical_tables,
            cte_aliases,
            allowed_columns,
            normalized_schema,
        )


def apply_row_level_security(sql: str, user: UserContext) -> str:
    statement = _parse_one(sql)
    if user.role in {UserRole.SALES_ANALYST, UserRole.SALES_MANAGER}:
        return statement.sql(dialect="sqlite").rstrip().removesuffix(";").rstrip()

    if user.employee_id is None:
        raise AccessDeniedError("ACCOUNT_MANAGER users must include employee_id")

    cte_aliases = _collect_cte_aliases(statement)
    collisions = set(RESTRICTED_TABLE_CTES) & cte_aliases
    if collisions:
        raise AccessDeniedError(
            f"CTE aliases cannot shadow row-restricted tables: {sorted(collisions)[0]}"
        )

    restricted_tables = {
        table.name.lower()
        for table in statement.find_all(exp.Table)
        if table.name.lower() in RESTRICTED_TABLE_CTES
    }
    if not restricted_tables:
        return statement.sql(dialect="sqlite").rstrip().removesuffix(";").rstrip()

    transformed = statement.copy()

    def replace_restricted_table(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Table) and node.name.lower() in RESTRICTED_TABLE_CTES:
            table_name = node.name.lower()
            has_alias = bool(node.args.get("alias"))
            node = node.copy()
            node.set("this", exp.to_identifier(RESTRICTED_TABLE_CTES[table_name]))
            node.set("db", None)
            node.set("catalog", None)
            if not has_alias:
                node.set("alias", exp.TableAlias(this=exp.to_identifier(table_name)))
            return node
        return node

    transformed = transformed.transform(replace_restricted_table)
    transformed_sql = transformed.sql(dialect="sqlite").rstrip().removesuffix(";").rstrip()
    employee_id = user.employee_id
    rbac_sql = f"""
WITH
  __rbac_customers AS (
    SELECT *
    FROM customers
    WHERE account_manager_id = {employee_id}
  ),
  __rbac_orders AS (
    SELECT orders.*
    FROM orders
    JOIN customers ON orders.customer_id = customers.customer_id
    WHERE customers.account_manager_id = {employee_id}
  ),
  __rbac_order_items AS (
    SELECT order_items.*
    FROM order_items
    JOIN orders ON order_items.order_id = orders.order_id
    JOIN customers ON orders.customer_id = customers.customer_id
    WHERE customers.account_manager_id = {employee_id}
  )
SELECT *
FROM (
  {transformed_sql}
) AS __rbac_result
"""
    return sqlglot.parse_one(rbac_sql, read="sqlite").sql(dialect="sqlite").rstrip().removesuffix(";").rstrip()


class RBACEnforcer:
    def enforce(
        self,
        sql: str,
        user: UserContext,
        physical_schema: dict[str, set[str]],
    ) -> RBACValidationResult:
        policy = get_role_policy(user)
        guardrails = SQLGuardrails()
        allowed_tables = set(policy.allowed_tables)

        try:
            validation = guardrails.validate(sql, allowed_tables)
        except UnsafeSQLError as exc:
            _raise_rbac_or_guardrail_error(exc)

        validate_column_access(validation.normalized_sql, user, physical_schema)
        secured_sql = apply_row_level_security(validation.normalized_sql, user)

        try:
            secured_validation = guardrails.validate(secured_sql, allowed_tables)
        except UnsafeSQLError as exc:
            _raise_rbac_or_guardrail_error(exc)

        row_level_security_applied = (
            user.role == UserRole.ACCOUNT_MANAGER
            and secured_sql != validation.normalized_sql
        )
        rbac_checks = [
            "Role policy loaded",
            "Table access validated by SQL guardrails",
            "Column access validated",
            "Row-level security applied" if row_level_security_applied else "No row-level filter required",
            "Transformed SQL revalidated by SQL guardrails",
        ]
        guardrail_checks = validation.checks + secured_validation.checks
        return RBACValidationResult(
            sql=secured_sql,
            role=user.role,
            allowed_tables=allowed_tables,
            row_level_security_applied=row_level_security_applied,
            checks=rbac_checks,
            guardrail_checks=guardrail_checks,
            rbac_checks=rbac_checks,
        )


def _parse_one(sql: str) -> exp.Expression:
    try:
        return sqlglot.parse_one(sql, read="sqlite")
    except sqlglot.errors.SqlglotError as exc:
        raise AccessDeniedError(f"SQL could not be parsed for RBAC validation: {exc}") from exc


def _raise_rbac_or_guardrail_error(error: UnsafeSQLError) -> None:
    message = str(error)
    if "Unknown or unauthorized table" in message:
        raise AccessDeniedError(message) from error
    raise error


def _normalize_schema(physical_schema: dict[str, set[str]]) -> dict[str, set[str]]:
    return {
        table.lower(): {column.lower() for column in columns}
        for table, columns in physical_schema.items()
    }


def _collect_cte_aliases(statement: exp.Expression) -> set[str]:
    return {
        cte.alias_or_name.lower()
        for cte in statement.find_all(exp.CTE)
        if cte.alias_or_name
    }


def _collect_table_aliases(
    statement: exp.Expression,
    cte_aliases: set[str],
    physical_schema: dict[str, set[str]],
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in statement.find_all(exp.Table):
        table_name = table.name.lower()
        if table_name in cte_aliases or table_name not in physical_schema:
            continue
        aliases[table.alias_or_name.lower()] = table_name
        aliases[table_name] = table_name
    return aliases


def _validate_star_access(
    star: exp.Star,
    alias_to_table: dict[str, str],
    physical_tables: set[str],
    allowed_columns: dict[str, set[str]],
    physical_schema: dict[str, set[str]],
) -> None:
    parent = star.parent
    qualifier = parent.table.lower() if isinstance(parent, exp.Column) and parent.table else ""
    tables = {alias_to_table[qualifier]} if qualifier in alias_to_table else physical_tables

    for table in tables:
        unauthorized = physical_schema.get(table, set()) - allowed_columns.get(table, set())
        if unauthorized:
            raise AccessDeniedError(
                f"SELECT * is not allowed for table '{table}' because columns are restricted"
            )


def _validate_column(
    column: exp.Column,
    alias_to_table: dict[str, str],
    physical_tables: set[str],
    cte_aliases: set[str],
    allowed_columns: dict[str, set[str]],
    physical_schema: dict[str, set[str]],
) -> None:
    column_name = column.name.lower()
    table_qualifier = column.table.lower() if column.table else ""

    if table_qualifier:
        if table_qualifier in cte_aliases:
            return
        table_name = alias_to_table.get(table_qualifier)
        if table_name is None:
            raise AccessDeniedError(f"Unknown or unauthorized table qualifier: {table_qualifier}")
        _require_allowed_column(table_name, column_name, allowed_columns)
        return

    candidate_tables = {
        table
        for table in physical_tables
        if column_name in physical_schema.get(table, set())
    }
    if len(candidate_tables) > 1:
        raise AccessDeniedError(f"Ambiguous unqualified column: {column.name}")
    if len(candidate_tables) == 1:
        _require_allowed_column(next(iter(candidate_tables)), column_name, allowed_columns)


def _require_allowed_column(
    table_name: str,
    column_name: str,
    allowed_columns: dict[str, set[str]],
) -> None:
    if column_name not in allowed_columns.get(table_name, set()):
        raise AccessDeniedError(f"Column '{table_name}.{column_name}' is not allowed for this role")
