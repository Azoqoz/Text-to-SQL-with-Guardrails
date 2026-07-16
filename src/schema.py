from __future__ import annotations

import sqlite3


def get_table_names(connection: sqlite3.Connection) -> list[str]:
    """Return user-created table names sorted alphabetically."""
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def get_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[dict[str, object]]:
    """Return column metadata for a table."""
    rows = connection.execute(f"PRAGMA table_info({_quote_identifier(table_name)})").fetchall()
    return [
        {
            "name": row[1],
            "type": row[2],
            "not_null": bool(row[3]),
            "default": row[4],
            "primary_key": bool(row[5]),
        }
        for row in rows
    ]


def get_foreign_keys(
    connection: sqlite3.Connection,
    table_name: str,
) -> list[dict[str, str]]:
    """Return foreign-key metadata for a table."""
    rows = connection.execute(f"PRAGMA foreign_key_list({_quote_identifier(table_name)})").fetchall()
    return [
        {
            "from_column": str(row[3]),
            "referenced_table": str(row[2]),
            "referenced_column": str(row[4]),
        }
        for row in rows
    ]


def get_physical_schema(connection: sqlite3.Connection) -> dict[str, set[str]]:
    """Return user table names mapped to their physical column names."""
    return {
        table_name.lower(): {
            str(column["name"]).lower()
            for column in get_table_columns(connection, table_name)
        }
        for table_name in get_table_names(connection)
    }


def build_schema_description(
    connection: sqlite3.Connection,
    allowed_tables: set[str] | None = None,
    allowed_columns: dict[str, set[str]] | None = None,
) -> str:
    """Build a readable schema description for future prompting."""
    lines: list[str] = ["Database schema:"]
    allowed_table_lookup = {table.lower() for table in allowed_tables or set()}
    allowed_column_lookup = {
        table.lower(): {column.lower() for column in columns}
        for table, columns in (allowed_columns or {}).items()
    }

    for table_name in get_table_names(connection):
        table_key = table_name.lower()
        if allowed_tables is not None and table_key not in allowed_table_lookup:
            continue

        lines.append(f"\nTable: {table_name}")
        lines.append("Columns:")

        for column in get_table_columns(connection, table_name):
            column_name = str(column["name"])
            if (
                allowed_columns is not None
                and column_name.lower() not in allowed_column_lookup.get(table_key, set())
            ):
                continue

            attributes = [str(column["type"]) or "UNKNOWN"]
            if column["primary_key"]:
                attributes.append("primary key")
            if column["not_null"]:
                attributes.append("not null")
            else:
                attributes.append("nullable")
            if column["default"] is not None:
                attributes.append(f"default {column['default']}")

            lines.append(f"- {column['name']} ({', '.join(attributes)})")

        foreign_keys = get_foreign_keys(connection, table_name)
        if allowed_tables is not None:
            foreign_keys = [
                foreign_key
                for foreign_key in foreign_keys
                if foreign_key["referenced_table"].lower() in allowed_table_lookup
                and (
                    allowed_columns is None
                    or foreign_key["from_column"].lower() in allowed_column_lookup.get(table_key, set())
                )
                and (
                    allowed_columns is None
                    or foreign_key["referenced_column"].lower()
                    in allowed_column_lookup.get(foreign_key["referenced_table"].lower(), set())
                )
            ]
        if foreign_keys:
            lines.append("Foreign keys:")
            for foreign_key in foreign_keys:
                lines.append(
                    "- "
                    f"{foreign_key['from_column']} -> "
                    f"{foreign_key['referenced_table']}.{foreign_key['referenced_column']}"
                )

    return "\n".join(lines)


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'
