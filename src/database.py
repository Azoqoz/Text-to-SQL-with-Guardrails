from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from src.guardrails import SQLValidationResult
from src.rbac import RBACValidationResult, UserContext, build_allowed_schema
from src.schema import build_schema_description, get_physical_schema, get_table_names


@dataclass(frozen=True)
class QueryExecutionResult:
    rows: list[dict[str, object]]
    row_count: int
    truncated: bool


class SQLiteReadOnlyDatabase:
    def __init__(self, database_path: Path, max_result_rows: int = 200) -> None:
        self.database_path = database_path.resolve()
        if not self.database_path.exists():
            raise FileNotFoundError(f"Database does not exist: {self.database_path}")
        if not 1 <= max_result_rows <= 10_000:
            raise ValueError("max_result_rows must be between 1 and 10,000")

        self.max_result_rows = max_result_rows

    def _connect_read_only(self) -> sqlite3.Connection:
        uri = f"file:{quote(str(self.database_path), safe=':/')}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def get_schema(self) -> str:
        with self._connect_read_only() as connection:
            return build_schema_description(connection)

    def get_table_names(self) -> list[str]:
        with self._connect_read_only() as connection:
            return get_table_names(connection)

    def get_physical_schema(self) -> dict[str, set[str]]:
        with self._connect_read_only() as connection:
            return get_physical_schema(connection)

    def get_schema_for_user(self, user: UserContext) -> str:
        with self._connect_read_only() as connection:
            return build_allowed_schema(connection, user)

    def execute_select(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> QueryExecutionResult:
        self._validate_select_sql(sql)
        return self._execute_read_only(sql, parameters)

    def execute_validated(
        self,
        validation: SQLValidationResult,
        parameters: tuple[Any, ...] = (),
    ) -> QueryExecutionResult:
        return self._execute_read_only(validation.normalized_sql, parameters)

    def execute_authorized(
        self,
        rbac_result: RBACValidationResult,
        parameters: tuple[Any, ...] = (),
    ) -> QueryExecutionResult:
        return self._execute_read_only(rbac_result.sql, parameters)

    def _execute_read_only(
        self,
        sql: str,
        parameters: tuple[Any, ...] = (),
    ) -> QueryExecutionResult:
        with self._connect_read_only() as connection:
            cursor = connection.execute(sql, parameters)
            fetched_rows = cursor.fetchmany(self.max_result_rows + 1)

        truncated = len(fetched_rows) > self.max_result_rows
        rows = [dict(row) for row in fetched_rows[: self.max_result_rows]]
        return QueryExecutionResult(rows=rows, row_count=len(rows), truncated=truncated)

    def _validate_select_sql(self, sql: str) -> None:
        stripped_sql = sql.strip()
        if not stripped_sql:
            raise ValueError("SQL must not be empty")

        first_keyword = stripped_sql.split(maxsplit=1)[0].upper()
        if first_keyword not in {"SELECT", "WITH"}:
            raise ValueError("Only SELECT and WITH statements are allowed")

        if _has_multiple_statements(stripped_sql):
            raise ValueError("Multiple SQL statements are not allowed")


def _has_multiple_statements(sql: str) -> bool:
    statement_end = _first_statement_semicolon_index(sql)
    if statement_end is None:
        return False

    remainder = sql[statement_end + 1 :].strip()
    return bool(remainder)


def _first_statement_semicolon_index(sql: str) -> int | None:
    quote_char: str | None = None
    index = 0

    while index < len(sql):
        char = sql[index]
        next_char = sql[index + 1] if index + 1 < len(sql) else ""

        if quote_char is not None:
            if char == quote_char:
                if next_char == quote_char:
                    index += 2
                    continue
                quote_char = None
            index += 1
            continue

        if char in {"'", '"'}:
            quote_char = char
            index += 1
            continue

        if char == "-" and next_char == "-":
            newline_index = sql.find("\n", index + 2)
            if newline_index == -1:
                return None
            index = newline_index + 1
            continue

        if char == "/" and next_char == "*":
            end_comment_index = sql.find("*/", index + 2)
            if end_comment_index == -1:
                return None
            index = end_comment_index + 2
            continue

        if char == ";":
            return index

        index += 1

    return None
