from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "company.db"
RANDOM_SEED = 42


EMPLOYEES = [
    (1, "Amina Saleh", "Sales", "2020-03-16"),
    (2, "Omar Haddad", "Operations", "2019-07-08"),
    (3, "Leila Mansour", "Sales", "2021-11-22"),
    (4, "Karim Nassar", "Operations", "2018-05-14"),
]

PRODUCTS = [
    (1, "Analytics Starter", "Software", 149.00),
    (2, "Analytics Pro", "Software", 349.00),
    (3, "Data Quality Audit", "Services", 900.00),
    (4, "Workflow Automation", "Software", 499.00),
    (5, "Cloud Backup Pack", "Infrastructure", 199.00),
    (6, "Priority Support", "Support", 120.00),
    (7, "Implementation Workshop", "Services", 1250.00),
    (8, "Security Review", "Services", 1500.00),
]

REGIONS = ["Central", "Eastern", "Western", "Northern", "Southern"]
STATUSES = ["Completed", "Pending", "Cancelled"]


def create_sample_database(database_path: Path = DEFAULT_DATABASE_PATH) -> None:
    """Create a deterministic SQLite sample database."""
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    if database_path.exists():
        database_path.unlink()

    random.seed(RANDOM_SEED)

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        _create_schema(connection)
        _insert_seed_data(connection)
        connection.commit()

    _print_summary(database_path)


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE employees (
            employee_id INTEGER PRIMARY KEY,
            employee_name TEXT NOT NULL,
            department TEXT NOT NULL,
            hire_date TEXT NOT NULL
        );

        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            customer_name TEXT NOT NULL,
            region TEXT NOT NULL,
            signup_date TEXT NOT NULL,
            account_manager_id INTEGER NOT NULL,
            FOREIGN KEY (account_manager_id) REFERENCES employees (employee_id)
        );

        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit_price REAL NOT NULL
        );

        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        );

        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            FOREIGN KEY (order_id) REFERENCES orders (order_id),
            FOREIGN KEY (product_id) REFERENCES products (product_id)
        );

        CREATE INDEX idx_orders_customer_id ON orders (customer_id);
        CREATE INDEX idx_orders_order_date ON orders (order_date);
        CREATE INDEX idx_order_items_order_id ON order_items (order_id);
        CREATE INDEX idx_order_items_product_id ON order_items (product_id);
        """
    )


def _insert_seed_data(connection: sqlite3.Connection) -> None:
    customers = _build_customers()
    orders = _build_orders(order_count=200)
    order_items = _build_order_items(orders)

    connection.executemany(
        """
        INSERT INTO employees (employee_id, employee_name, department, hire_date)
        VALUES (?, ?, ?, ?)
        """,
        EMPLOYEES,
    )
    connection.executemany(
        """
        INSERT INTO customers (
            customer_id,
            customer_name,
            region,
            signup_date,
            account_manager_id
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        customers,
    )
    connection.executemany(
        """
        INSERT INTO products (product_id, product_name, category, unit_price)
        VALUES (?, ?, ?, ?)
        """,
        PRODUCTS,
    )
    connection.executemany(
        """
        INSERT INTO orders (order_id, customer_id, order_date, status)
        VALUES (?, ?, ?, ?)
        """,
        orders,
    )
    connection.executemany(
        """
        INSERT INTO order_items (
            order_item_id,
            order_id,
            product_id,
            quantity,
            unit_price
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        order_items,
    )


def _build_customers() -> list[tuple[int, str, str, str, int]]:
    names = [
        "Blue Harbor Logistics",
        "Summit Retail Group",
        "Cedar Health Partners",
        "Northstar Manufacturing",
        "Brightline Education",
        "Urban Basket Markets",
        "Falcon Energy Services",
        "Greenfield Properties",
        "Silverline Finance",
        "Vertex Telecom",
        "Atlas Food Supply",
        "MetroFleet Transport",
        "Palm Grove Hotels",
        "Horizon Media Labs",
        "NobleCare Clinics",
        "Redwood Construction",
        "Clearwater Utilities",
        "Prime Office Solutions",
        "NovaTech Systems",
        "Everwell Pharmacies",
    ]
    start_date = date(2023, 1, 1)

    customers = []
    for index, name in enumerate(names, start=1):
        signup_date = start_date + timedelta(days=random.randint(0, 690))
        customers.append(
            (
                index,
                name,
                REGIONS[(index - 1) % len(REGIONS)],
                signup_date.isoformat(),
                random.choice(EMPLOYEES)[0],
            )
        )

    return customers


def _build_orders(order_count: int) -> list[tuple[int, int, str, str]]:
    start_date = date(2024, 1, 1)
    end_date = date(2025, 12, 31)
    span_days = (end_date - start_date).days

    orders = []
    for order_id in range(1, order_count + 1):
        order_date = start_date + timedelta(days=random.randint(0, span_days))
        status = random.choices(STATUSES, weights=[75, 18, 7], k=1)[0]
        orders.append((order_id, random.randint(1, 20), order_date.isoformat(), status))

    return orders


def _build_order_items(
    orders: list[tuple[int, int, str, str]],
) -> list[tuple[int, int, int, int, float]]:
    product_prices = {product_id: price for product_id, _, _, price in PRODUCTS}
    order_items = []
    order_item_id = 1

    for order_id, _, _, _ in orders:
        item_count = random.randint(2, 4)
        product_ids = random.sample(list(product_prices), k=item_count)
        for product_id in product_ids:
            order_items.append(
                (
                    order_item_id,
                    order_id,
                    product_id,
                    random.randint(1, 8),
                    product_prices[product_id],
                )
            )
            order_item_id += 1

    return order_items


def _print_summary(database_path: Path) -> None:
    print(f"Database created at: {database_path}")
    with sqlite3.connect(database_path) as connection:
        for table_name in ("employees", "customers", "products", "orders", "order_items"):
            row_count = connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
            print(f"{table_name}: {row_count}")


if __name__ == "__main__":
    create_sample_database()
