# Migration backend

Run from the repository root with the existing environment settings:

```sh
python -m pip install -r requirements.txt
python -m uvicorn src.api:create_app --factory --host 127.0.0.1 --port 8000
```

The application factory reads `AppSettings` once. `public_demo` remains the
default. The configured SQLite database must already exist; the API never creates
or seeds it. `/health` is a liveness check and does not open the database.

`src.application.TextToSQLApplication` is independent of FastAPI and Streamlit.
It constructs a request-scoped provider and delegates to the existing
`TextToSQLService`. Schema filtering, SQLGlot validation, table/column allowlists,
RBAC, row-level rewriting, transformed-SQL validation, and read-only execution
remain in the existing modules. Query execution is synchronous: the HTTP response
contains the completed result, with no jobs, polling, streaming, or background work.

## Endpoints

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | `{"status":"ok"}` |
| GET | `/capabilities?role=sales_analyst` | Mode, row cap, provider defaults, role options, example questions, and selected role's filtered schema |
| POST | `/query` | Existing `TextToSQLResponse`, including generated/authorized SQL, rows, truncation, checks, and safe errors |

For account-manager capabilities, also supply `employee_id=1` (or another positive
employee ID). Capabilities default to `sales_analyst`. No physical schema, database
path, saved credential, or arbitrary SQL execution endpoint is exposed. Interactive
API documentation routes are disabled to keep the exposed surface to these three routes.

Demo query body:

```json
{
  "question": "Show my assigned customers.",
  "user_id": "demo_account_manager_1",
  "role": "account_manager",
  "employee_id": 1
}
```

In `local_full`, optional fields are `provider`, `model_name`, `api_key`, and
`ollama_host`. Omitted provider/model/host values use `AppSettings`. Cloud providers
require a key on each request; keys do not fall back to environment variables.
Ollama requires no key. Provider failure never automatically falls back to demo.
Explicit local use of the demo adapter remains supported by the existing factory;
if configured as the local default, it is also included in capabilities.
In public demo, valid provider overrides are ignored and the question must match
the selected role's example catalog, as in the current UI. The catalog is mirrored
from Streamlit with a parity test so this migration does not modify its UI file.

Roles and employee IDs retain the current UI's caller-selected demo/local behavior.
They are not authenticated identities; this layer does not introduce authentication.

Successful HTTP requests return status 200 with the existing query result envelope,
including handled query failures (`success: false`). Invalid HTTP shapes or user
contexts return 422 with a safe `error_type`/`error_message` pair. Unavailable schema
metadata returns 503; unexpected HTTP failures return a generic 500. Validation
responses omit input values. Responses use `Cache-Control: no-store`.

Keys are used only to construct the current provider, excluded from request model
serialization/repr, and never retained in application state or written to disk.
Provider errors are sanitized and reflected credentials are redacted from query
output. The API adds no request-body logging.

## Tests

```sh
python -m pytest tests/backend -q
python -m pytest tests -q
```

Backend tests use temporary databases and injected provider clients. They compare
all demo questions and a role/SQL matrix directly with the existing service,
exercise real HTTP serialization, and check credential handling and rejection
before execution. Existing migration parity tests remain unchanged.
