# Text-to-SQL with Guardrails

A secure multi-provider Text-to-SQL application that converts natural-language business questions into SQLite queries, validates them through AST-based SQL guardrails, enforces role-based access control, and executes only authorized queries through a read-only database connection.

## Overview

This project is designed for an authorized internal business employee asking natural-language questions over structured company data. The application sends only a role-filtered schema to the selected LLM provider, keeps generated SQL transparent, and never executes generated SQL directly. Before execution, SQLGlot guardrails and backend RBAC apply table, column, and row-level controls.

## Demo Workflow

User Question -> Role-Filtered Schema -> Selected LLM Provider -> Generated SQL -> SQLGlot Guardrails -> RBAC Enforcement -> Read-only SQLite Execution -> Results

## Key Features

- Multi-provider LLM support
- OpenAI, Gemini, Claude, and Ollama
- SQLite schema inspection
- Role-filtered schema exposure
- AST-based SQL validation
- SELECT-only policy
- Table allowlisting
- Column-level permissions
- Row-level security
- Read-only execution
- Result limiting
- Generated and authorized SQL transparency
- Streamlit UI
- Safe error handling
- Automated tests

## Roles and Permissions

Sales Analyst:
- Access to customers, orders, order_items, and products
- No access to the employees table
- Restricted customer columns
- No row-level restriction

Sales Manager:
- Access to all sales tables
- Access to the employees table
- Broad sales access
- No row-level restriction

Account Manager:
- Access only to assigned customers
- Access only to related orders and order items
- Products remain available
- Backend-enforced row-level security

Role selection in the demo is not production authentication.

## Security Architecture

Defense in depth:

1. Role-filtered schema sent to the LLM
2. Shared prompt restrictions
3. SQLGlot AST parsing
4. One-statement policy
5. SELECT-only policy
6. Table allowlisting
7. Column-level validation
8. Row-level security rewriting
9. Re-validation after rewriting
10. SQLite `mode=ro`
11. `PRAGMA query_only`
12. Result row limiting

## Architecture

```text
Text-to-SQL-with-Guardrails/
├── app.py
├── assets/
│   ├── .gitkeep
│   ├── account-manager-rls.png
│   ├── sales-analyst-query.png
│   └── sales-manager-query.png
├── data/
│   ├── .gitkeep
│   └── company.db
├── scripts/
│   └── create_sample_db.py
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── guardrails.py
│   ├── models.py
│   ├── rbac.py
│   ├── schema.py
│   ├── service.py
│   └── providers/
│       ├── __init__.py
│       ├── anthropic_provider.py
│       ├── base.py
│       ├── factory.py
│       ├── gemini_provider.py
│       ├── models.py
│       ├── ollama_provider.py
│       ├── openai_provider.py
│       └── prompt.py
├── tests/
│   ├── test_config.py
│   ├── test_database.py
│   ├── test_guardrails.py
│   ├── test_providers.py
│   ├── test_rbac.py
│   ├── test_sample_database.py
│   └── test_service.py
├── .env.example
├── .gitignore
├── README.md
└── requirements.txt
```

## Sample Database

The included SQLite database contains fictional business data:

- employees
- customers
- products
- orders
- order_items

Orders cover 2024 and 2025 with multiple regions and order statuses. The dataset is suitable for joins, aggregation, rankings, date analysis, and role-based filtering demonstrations.

## Supported Providers

- OpenAI
- Gemini
- Claude via Anthropic
- Ollama locally

Cloud providers require the user's own API key. Ollama requires a local Ollama server. API keys are entered through the UI or environment variables and are never committed.

Example model names vary by provider and availability. Enter the model name you intend to use in the UI.

## Extensibility and Real-World Use

> The project currently supports SQLite, while its modular architecture is designed to be extended to PostgreSQL, MySQL, SQL Server, and other relational databases through database-specific adapters.

The current implementation is fully functional with SQLite. The application architecture separates LLM providers, schema inspection, SQL validation, RBAC enforcement, database execution, and the Streamlit UI, which makes the project suitable as a foundation for other relational databases.

Supporting PostgreSQL, MySQL, SQL Server, or another database would require a database-specific adapter, the matching SQLGlot dialect, database-specific schema inspection, a read-only database user, and adapted row-level security and query execution where needed. Other databases are not supported out of the box in this repository.

In practical business use, authorized employees can ask questions about structured company data without writing SQL. This can reduce repeated manual reporting requests and help sales, operations, finance, and management teams explore approved data more quickly. Guardrails and RBAC provide a safer alternative to executing unrestricted LLM-generated SQL, and the same architecture could be integrated with an internal analytics platform or company database after adapting the database layer and production security controls.

## Installation

PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\activate
py -m pip install -r requirements.txt
py scripts/create_sample_db.py
py -m streamlit run app.py
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/create_sample_db.py
python -m streamlit run app.py
```

## Environment Configuration

`.env.example`:

```env
DATABASE_PATH=data/company.db
MAX_RESULT_ROWS=200

LLM_PROVIDER=openai

OPENAI_API_KEY=
OPENAI_MODEL=

GEMINI_API_KEY=
GEMINI_MODEL=

ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=

OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=
```

Users can also enter API keys and model names directly through the Streamlit UI without editing `.env`.

## Example Questions

Sales Analyst:
- What are the top 5 products by total completed-order revenue?
- Show monthly completed-order revenue for 2025.
- Which regions have the highest number of completed orders?
- Which customers placed the most completed orders?

Sales Manager:
- Which employees manage the highest-revenue customer portfolios?
- Show customer count by account manager.
- Compare completed-order revenue by region.
- Which products generate the most revenue?

Account Manager:
- Show my assigned customers.
- Which of my customers generated the most revenue?
- Show completed orders for my customers.
- What products were purchased most by my customers?

## Testing

Standard command:

```powershell
py -m pytest
```

Windows temp-folder workaround used on this machine:

```powershell
$root = "D:\pytest_aziz"
New-Item -ItemType Directory -Path $root -Force | Out-Null
$run = Join-Path $root ("run_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
py -m pytest --basetemp="$run"
```

The external temp folder is only a workaround for local Windows permission issues.

## Screenshots

### Account Manager — Row-Level Security

![Account Manager query with backend-enforced row-level security](assets/account-manager-rls.png)

### Sales Analyst — Aggregated Sales Query

![Sales Analyst query over aggregated sales data](assets/sales-analyst-query.png)

### Sales Manager — Broader Employee and Sales Access

![Sales Manager query with broader employee and sales access](assets/sales-manager-query.png)

## Limitations

- LLM SQL may be safe but logically incorrect
- Demo role selector is not authentication
- SQLite-focused implementation; other databases are not supported out of the box
- No production identity provider
- No query cost estimator
- No sensitive-data masking beyond configured permissions
- API availability and model names vary by provider
- Production integration requires database-specific testing, authentication, secrets management, auditing, least-privilege credentials, rate limiting, and stronger authorization integration

## Future Improvements

- Real authentication
- Identity-provider integration
- Policy storage in a database
- Audit logs
- Query timeout and complexity limits
- Sensitive-column masking
- Evaluation dataset and execution accuracy metrics
- PostgreSQL support
- FastAPI backend
- Deployment

## Tech Stack

Python, Streamlit, SQLite, SQLGlot, Pandas, Pytest, OpenAI SDK, Google GenAI SDK, Anthropic SDK, Ollama SDK

## Project Status

The local application is complete, and the automated test suite currently includes 146 passing tests. Public deployment has not been added.

## CV Bullet

Built a secure multi-provider Text-to-SQL application that translates natural-language business questions into SQLite queries, validates generated SQL through AST-based guardrails, enforces role-based table, column, and row-level permissions, and executes only authorized queries through a read-only database connection.
