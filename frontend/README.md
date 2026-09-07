# Query Foundry

Next.js App Router + TypeScript frontend for the existing FastAPI migration backend.

## Run

Start the existing Python API from the repository root in its installed environment:

```sh
python -m uvicorn src.api:create_app --factory --host 127.0.0.1 --port 8000
```

Then, in `frontend/`:

```sh
npm install
cp .env.example .env.local
npm run dev
```

On PowerShell, use `Copy-Item .env.example .env.local`. Open `http://localhost:3000`.
The API needs its existing configured database. The frontend never creates or changes data.

For production, run `npm run build` and `npm start`. On Vercel, set the root
directory to `frontend` and configure `NEXT_PUBLIC_API_BASE_URL` to your reachable
FastAPI base URL before building. The localhost example is for local development.
Restart/rebuild when changing the URL. Do not put credentials in this URL.

## Architecture

- `app/`: page, root layout, error boundary, local Geist font, and global responsive styles.
- `components/`: request workflow, access manifest, SQL inspection, governance evidence, result deck.
- `lib/types.ts` and `contracts.ts`: exact API DTOs and runtime response checks.
- `lib/api.ts`: health/capability/query client, no-store requests, aborts, finite timeouts, safe error handling.
- `lib/presentation.ts`: schema-text presentation, backend diagnostic mapping, result formatting, safe CSV export.
- `next.config.ts`: three standard same-origin rewrites to the environment-configured
  FastAPI URL. This avoids browser CORS changes and adds no security decision logic.

Capabilities populate roles, provider controls, model defaults, examples, and schema.
Role and employee-scope changes reload capabilities; stale requests are discarded and
previous results are cleared. The query route receives the existing DTO unchanged.
No query is automatically retried. The SQL surface is display-only; copying preserves
the original backend SQL, while formatting and highlighting affect presentation only.

Keys live in React memory until submission, are cleared immediately, and are released
from the request object on completion. There is no local/session storage, cookie,
credential URL parameter, logging, saved request history, or frontend analytics.
Production HTTPS and ordinary secure backend hosting remain deployment responsibilities.

## Backend truth and limits

- `success: true` authorizes the green result state; HTTP 200 alone never does.
- `unsafe_sql` / `access_denied` produce BLOCKED. Provider, configuration, validation,
  and database failures have separate caution states and no result table.
- Checkpoints say VERIFIED only for matching returned diagnostic strings. Capability
  scope and configured row caps are labeled as scope/cap, not successful checks.
- The API exposes schema as text and has no structured policy report, per-stage progress,
  editable-SQL execution, or pre-query row-filter description. The UI does not invent these.
- Row-filter application is shown only from `row_level_security_applied` after execution.
- Caller-selected roles/employee IDs preserve the current demo/local model; they are
  not authenticated identities. The backend controls the mode, and the frontend has no mode switch.
- Synchronous queries are subject to the hosting platform's proxy/request timeout. Configure
  hosting appropriately for local model latency; the frontend does not retry timed-out execution.

## Validate

```sh
npm run lint
npm test
npm run build
```

Run the unchanged Python suite from the repository root: `python -m pytest tests -q`.
