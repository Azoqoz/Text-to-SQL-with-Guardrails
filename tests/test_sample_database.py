import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.create_sample_db import REGIONS, STATUSES, create_sample_database


EXPECTED_TABLES = {"employees", "customers", "products", "orders", "order_items"}


def test_sample_database_creation(tmp_path: Path) -> None:
    database_path = tmp_path / "company.db"

    create_sample_database(database_path)

    assert database_path.exists()

    with sqlite3.connect(database_path) as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert EXPECTED_TABLES.issubset(table_names)

        assert _count_rows(connection, "employees") == 4
        assert _count_rows(connection, "customers") == 20
        assert _count_rows(connection, "products") == 8

        order_count = _count_rows(connection, "orders")
        order_item_count = _count_rows(connection, "order_items")
        assert order_count >= 180
        assert order_item_count > order_count

        years = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT substr(order_date, 1, 4) FROM orders"
            ).fetchall()
        }
        assert {"2024", "2025"}.issubset(years)

        statuses = {
            row[0]
            for row in connection.execute("SELECT DISTINCT status FROM orders").fetchall()
        }
        assert statuses.issubset(set(STATUSES))

        regions = {
            row[0]
            for row in connection.execute("SELECT DISTINCT region FROM customers").fetchall()
        }
        assert regions.issubset(set(REGIONS))


def test_foreign_keys_are_enabled_during_creation(tmp_path: Path) -> None:
    database_path = tmp_path / "company.db"

    create_sample_database(database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO orders (order_id, customer_id, order_date, status)
                VALUES (9999, 9999, '2025-01-01', 'Completed')
                """
            )


def _count_rows(connection: sqlite3.Connection, table_name: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
