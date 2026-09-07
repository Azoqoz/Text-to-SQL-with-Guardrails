from __future__ import annotations

from src.providers.base import TextToSQLProvider
from src.providers.models import GeneratedSQL, LLMResponseError


UNSUPPORTED_DEMO_QUESTION_MESSAGE = (
    "This public demo supports the example questions shown in the sidebar."
)


class DemoTextToSQLProvider(TextToSQLProvider):
    """Deterministic, offline SQL generator for the documented demo questions."""

    provider_name = "demo"
    model_name = "secure-demo-generator"

    _QUERIES: dict[str, tuple[str, str]] = {
        "what are the top 5 products by total completed-order revenue?": (
            """
            SELECT
                products.product_name,
                ROUND(SUM(order_items.quantity * order_items.unit_price), 2) AS total_revenue
            FROM products
            JOIN order_items ON order_items.product_id = products.product_id
            JOIN orders ON orders.order_id = order_items.order_id
            WHERE orders.status = 'Completed'
            GROUP BY products.product_id, products.product_name
            ORDER BY total_revenue DESC, products.product_name
            LIMIT 5
            """,
            "Ranks the five products with the most revenue from completed orders.",
        ),
        "show monthly completed-order revenue for 2025.": (
            """
            SELECT
                SUBSTR(orders.order_date, 1, 7) AS month,
                ROUND(SUM(order_items.quantity * order_items.unit_price), 2) AS total_revenue
            FROM orders
            JOIN order_items ON order_items.order_id = orders.order_id
            WHERE orders.status = 'Completed'
              AND orders.order_date >= '2025-01-01'
              AND orders.order_date < '2026-01-01'
            GROUP BY SUBSTR(orders.order_date, 1, 7)
            ORDER BY month
            """,
            "Summarizes completed-order revenue by month during 2025.",
        ),
        "which regions have the highest number of completed orders?": (
            """
            SELECT
                customers.region,
                COUNT(orders.order_id) AS completed_order_count
            FROM customers
            JOIN orders ON orders.customer_id = customers.customer_id
            WHERE orders.status = 'Completed'
            GROUP BY customers.region
            ORDER BY completed_order_count DESC, customers.region
            """,
            "Ranks regions by their number of completed orders.",
        ),
        "which customers placed the most completed orders?": (
            """
            SELECT
                customers.customer_name,
                COUNT(orders.order_id) AS completed_order_count
            FROM customers
            JOIN orders ON orders.customer_id = customers.customer_id
            WHERE orders.status = 'Completed'
            GROUP BY customers.customer_id, customers.customer_name
            ORDER BY completed_order_count DESC, customers.customer_name
            """,
            "Ranks customers by their number of completed orders.",
        ),
        "which employees manage the highest-revenue customer portfolios?": (
            """
            SELECT
                employees.employee_name,
                ROUND(SUM(order_items.quantity * order_items.unit_price), 2) AS portfolio_revenue
            FROM employees
            JOIN customers ON customers.account_manager_id = employees.employee_id
            JOIN orders ON orders.customer_id = customers.customer_id
            JOIN order_items ON order_items.order_id = orders.order_id
            WHERE orders.status = 'Completed'
            GROUP BY employees.employee_id, employees.employee_name
            ORDER BY portfolio_revenue DESC, employees.employee_name
            """,
            "Ranks employees by completed-order revenue across their customer portfolios.",
        ),
        "show customer count by account manager.": (
            """
            SELECT
                employees.employee_name,
                COUNT(customers.customer_id) AS customer_count
            FROM employees
            LEFT JOIN customers ON customers.account_manager_id = employees.employee_id
            GROUP BY employees.employee_id, employees.employee_name
            ORDER BY customer_count DESC, employees.employee_name
            """,
            "Counts assigned customers for each account manager.",
        ),
        "compare completed-order revenue by region.": (
            """
            SELECT
                customers.region,
                ROUND(SUM(order_items.quantity * order_items.unit_price), 2) AS total_revenue
            FROM customers
            JOIN orders ON orders.customer_id = customers.customer_id
            JOIN order_items ON order_items.order_id = orders.order_id
            WHERE orders.status = 'Completed'
            GROUP BY customers.region
            ORDER BY total_revenue DESC, customers.region
            """,
            "Compares completed-order revenue across customer regions.",
        ),
        "which products generate the most revenue?": (
            """
            SELECT
                products.product_name,
                ROUND(SUM(order_items.quantity * order_items.unit_price), 2) AS total_revenue
            FROM products
            JOIN order_items ON order_items.product_id = products.product_id
            JOIN orders ON orders.order_id = order_items.order_id
            WHERE orders.status = 'Completed'
            GROUP BY products.product_id, products.product_name
            ORDER BY total_revenue DESC, products.product_name
            """,
            "Ranks products by revenue from completed orders.",
        ),
        "show my assigned customers.": (
            """
            SELECT
                customers.customer_id,
                customers.customer_name,
                customers.region,
                customers.signup_date
            FROM customers
            ORDER BY customers.customer_name
            """,
            "Lists customers; backend row-level security limits the result to assigned customers.",
        ),
        "which of my customers generated the most revenue?": (
            """
            SELECT
                customers.customer_name,
                ROUND(SUM(order_items.quantity * order_items.unit_price), 2) AS total_revenue
            FROM customers
            JOIN orders ON orders.customer_id = customers.customer_id
            JOIN order_items ON order_items.order_id = orders.order_id
            WHERE orders.status = 'Completed'
            GROUP BY customers.customer_id, customers.customer_name
            ORDER BY total_revenue DESC, customers.customer_name
            """,
            "Ranks customers by completed-order revenue; backend row-level security limits customer access.",
        ),
        "show completed orders for my customers.": (
            """
            SELECT
                orders.order_id,
                orders.customer_id,
                orders.order_date,
                orders.status
            FROM orders
            WHERE orders.status = 'Completed'
            ORDER BY orders.order_date DESC, orders.order_id DESC
            """,
            "Lists completed orders; backend row-level security limits orders to assigned customers.",
        ),
        "what products were purchased most by my customers?": (
            """
            SELECT
                products.product_name,
                SUM(order_items.quantity) AS units_purchased
            FROM products
            JOIN order_items ON order_items.product_id = products.product_id
            JOIN orders ON orders.order_id = order_items.order_id
            JOIN customers ON customers.customer_id = orders.customer_id
            GROUP BY products.product_id, products.product_name
            ORDER BY units_purchased DESC, products.product_name
            """,
            "Ranks products by purchased units; backend row-level security limits customer access.",
        ),
    }

    @classmethod
    def supports_question(cls, question: str) -> bool:
        return cls._normalize_question(question) in cls._QUERIES

    def generate_sql(self, question: str, schema: str) -> GeneratedSQL:
        self._validate_inputs(question, schema)
        query = self._QUERIES.get(self._normalize_question(question))
        if query is None:
            raise LLMResponseError(UNSUPPORTED_DEMO_QUESTION_MESSAGE)

        sql, explanation = query
        return GeneratedSQL(
            sql=_normalize_sql_whitespace(sql),
            explanation=explanation,
            provider=self.provider_name,
            model=self.model_name,
        )

    @staticmethod
    def _normalize_question(question: str) -> str:
        return " ".join(question.split()).casefold()


class GuardrailDemoTextToSQLProvider(DemoTextToSQLProvider):
    """Public-demo catalog extension; the service must authorize every output."""

    _QUERIES = {
        **DemoTextToSQLProvider._QUERIES,
        "delete all customers from the database.": (
            "DELETE FROM customers",
            "Attempts to delete customers to exercise backend read-only enforcement.",
        ),
        "show all employees and their details.": (
            "SELECT employee_id, employee_name, department, hire_date FROM employees",
            "Requests employee details to exercise backend role access enforcement.",
        ),
    }


def _normalize_sql_whitespace(sql: str) -> str:
    return "\n".join(line.strip() for line in sql.strip().splitlines())
