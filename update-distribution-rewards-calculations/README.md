# update-accessibility-rewards

Standalone Deno endpoint that runs the accessibility-rewards calculation pipeline, migrated out of its original Next.js cron home.

## Requirements

- [Deno](https://deno.com) 2.x
- A Postgres database with the expected schema (see `src/db/schema.ts`)

## Setup

1. Create a `.env` file at the repo root:

```
   PORT=8000
   DATABASE_URL=postgres://user:pass@host:5432/dbname
   RPC_KEY=               # if applicable, used by ethers for on-chain reads (like pricing)
```

2. Wire up your database in `src/lib/database/db.ts`. The file ships as a stub; replace it with a real connection exposing `fullAccess` and `readOnly` pools.

## Running

```bash
deno task serve
```

Server listens on `http://localhost:$PORT` (default 8000).

## Endpoint

### `GET /api/update-distribution-rewards`

Runs the accessibility-rewards update for all configured farms.

- **Query params:**
    - `updateAll=true` — reprocess historical months as well as the current window. Defaults to current-month only.
- **Responses:**
    - `200` — JSON with per-farm results
    - `500` — pipeline failed; body includes `error` and `details`

Example:

```bash
curl "http://localhost:8000/api/update-distribution-rewards?updateAll=false"
```
