"""Small, independent data oracle for migration parity (never uses company.db)."""

import socket
import sqlite3
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.database import SQLiteReadOnlyDatabase
from src.models import TextToSQLRequest
from src.providers.base import TextToSQLProvider
from src.providers.models import GeneratedSQL
from src.rbac import UserContext, UserRole
from src.service import TextToSQLService


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError("Parity tests must not contact a provider or network")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "parity.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE employees (
                employee_id INTEGER PRIMARY KEY, employee_name TEXT,
                department TEXT, hire_date TEXT);
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY, customer_name TEXT, region TEXT,
                signup_date TEXT, account_manager_id INTEGER REFERENCES employees(employee_id));
            CREATE TABLE orders (
                order_id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(customer_id),
                order_date TEXT, status TEXT);
            CREATE TABLE products (
                product_id INTEGER PRIMARY KEY, product_name TEXT, category TEXT, unit_price REAL);
            CREATE TABLE order_items (
                order_item_id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(order_id),
                product_id INTEGER REFERENCES products(product_id), quantity INTEGER, unit_price REAL);
            CREATE TABLE payroll (employee_id INTEGER, salary REAL);
            INSERT INTO employees VALUES
                (1, 'Amina', 'Sales', '2024-01-01'), (2, 'Ben', 'Sales', '2024-01-01');
            INSERT INTO customers VALUES
                (1, 'Alpha', 'East', '2025-01-01', 1),
                (2, 'Beta', 'West', '2025-01-01', 2),
                (3, 'Gamma', 'East', '2025-01-01', 1);
            INSERT INTO orders VALUES
                (11, 1, '2025-01-10', 'Completed'),
                (12, 2, '2025-01-11', 'Completed'),
                (13, 3, '2025-02-10', 'Completed');
            INSERT INTO products VALUES (1, 'Widget', 'Tools', 10), (2, 'Gadget', 'Tools', 30);
            INSERT INTO order_items VALUES (101, 11, 1, 2, 10), (102, 12, 2, 3, 30), (103, 13, 1, 2, 10);
            INSERT INTO payroll VALUES (1, 99999);
        """)
    database = SQLiteReadOnlyDatabase(path)
    database.execute_authorized = Mock(wraps=database.execute_authorized)
    return database


@pytest.fixture
def run_sql(database):
    """Mock generation only; retain real schema, guardrails, RBAC and SQLite."""
    def run(sql, role=UserRole.SALES_ANALYST, employee_id=None):
        provider = Mock(spec=TextToSQLProvider)
        provider.provider_name = "parity"
        provider.model_name = "fixed-sql"
        provider.generate_sql.return_value = GeneratedSQL(sql, "Parity query.", "parity", "fixed-sql")
        user = UserContext("parity-user", role, employee_id)
        response = TextToSQLService(database, provider).answer(TextToSQLRequest("List data", user))
        return response, provider

    return run
