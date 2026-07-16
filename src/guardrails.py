from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


class UnsafeSQLError(ValueError):
    pass


@dataclass(frozen=True)
class SQLValidationResult:
    normalized_sql: str
    referenced_tables: set[str]
    checks: list[str]


class SQLGuardrails:
    _DISALLOWED_KEYWORDS = {
        "ALTER",
        "ANALYZE",
        "ATTACH",
        "BEGIN",
        "COMMIT",
        "CREATE",
        "DELETE",
        "DETACH",
        "DROP",
        "END",
        "INSERT",
        "LOAD_EXTENSION",
        "MERGE",
        "PRAGMA",
        "REINDEX",
        "REPLACE",
        "ROLLBACK",
        "TRANSACTION",
        "UPDATE",
        "VACUUM",
    }
    _DISALLOWED_EXPRESSIONS = {
        "Alter",
        "Analyze",
        "Attach",
        "Command",
        "Commit",
        "Create",
        "Delete",
        "Detach",
        "Drop",
        "Insert",
        "Merge",
        "Pragma",
        "Rollback",
        "Transaction",
        "Update",
    }

    def validate(self, sql: str, allowed_tables: set[str]) -> SQLValidationResult:
        stripped_sql = sql.strip()
        if not stripped_sql:
            raise UnsafeSQLError("SQL must not be empty")

        if any(comment_marker in stripped_sql for comment_marker in ("--", "/*", "*/")):
            raise UnsafeSQLError("SQL comments are not allowed")

        self._reject_disallowed_keywords(stripped_sql)

        try:
            statements = sqlglot.parse(stripped_sql, read="sqlite")
        except sqlglot.errors.SqlglotError as exc:
            raise UnsafeSQLError(f"SQL could not be parsed: {exc}") from exc

        if len(statements) != 1:
            raise UnsafeSQLError("Exactly one SQL statement is allowed")

        statement = statements[0]
        self._reject_disallowed_expressions(statement)
        if not self._is_allowed_query_expression(statement):
            raise UnsafeSQLError("Only read-only SELECT query expressions are allowed")

        cte_aliases = self._collect_cte_aliases(statement)
        referenced_tables = self._collect_physical_tables(statement, cte_aliases, allowed_tables)
        checks = [
            "SQL parsed successfully with SQLGlot SQLite dialect",
            "Exactly one statement detected",
            "Statement is a read-only query expression",
            "No dangerous SQL operation detected",
            "Referenced physical tables are allowlisted",
            "CTE aliases excluded from physical table checks",
        ]
        normalized_sql = statement.sql(dialect="sqlite").rstrip().removesuffix(";").rstrip()

        return SQLValidationResult(
            normalized_sql=normalized_sql,
            referenced_tables=referenced_tables,
            checks=checks,
        )

    def _reject_disallowed_keywords(self, sql: str) -> None:
        tokens = {
            token.strip(" \t\r\n(),;")
            .replace("-", "_")
            .upper()
            for token in sql.replace("\n", " ").split()
        }
        blocked = tokens & self._DISALLOWED_KEYWORDS
        if blocked:
            raise UnsafeSQLError(f"Disallowed SQL keyword: {sorted(blocked)[0]}")

    def _reject_disallowed_expressions(self, statement: exp.Expression) -> None:
        for node in statement.walk():
            if type(node).__name__ in self._DISALLOWED_EXPRESSIONS:
                raise UnsafeSQLError(f"Disallowed SQL expression: {type(node).__name__}")

    def _is_allowed_query_expression(self, statement: exp.Expression) -> bool:
        if isinstance(statement, exp.Select):
            return True

        if isinstance(statement, (exp.Union, exp.Intersect, exp.Except)):
            return self._is_allowed_query_expression(statement.left) and self._is_allowed_query_expression(
                statement.right
            )

        if isinstance(statement, exp.Subquery):
            return self._is_allowed_query_expression(statement.this)

        return False

    def _collect_cte_aliases(self, statement: exp.Expression) -> set[str]:
        aliases: set[str] = set()
        for cte in statement.find_all(exp.CTE):
            alias = cte.alias_or_name
            if alias:
                aliases.add(alias.lower())
        return aliases

    def _collect_physical_tables(
        self,
        statement: exp.Expression,
        cte_aliases: set[str],
        allowed_tables: set[str],
    ) -> set[str]:
        allowed_table_lookup = {table.lower(): table for table in allowed_tables}
        referenced_tables: set[str] = set()

        for table in statement.find_all(exp.Table):
            table_name = table.name
            if not table_name or table_name.lower() in cte_aliases:
                continue

            allowed_table_name = allowed_table_lookup.get(table_name.lower())
            if allowed_table_name is None:
                raise UnsafeSQLError(f"Unknown or unauthorized table: {table_name}")

            referenced_tables.add(allowed_table_name)

        return referenced_tables
