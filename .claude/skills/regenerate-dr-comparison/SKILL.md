---
name: regenerate-dr-comparison
description: Regenerate dune-results/dr_comparison_latest.xlsx end-to-end (re-execute Dune queries, refresh the Spark reference CSV, combine, compare). Use when the user asks to regenerate/refresh the DR comparison workbook, re-run the DR pipeline, or update DR results.
---

# Regenerate the DR comparison workbook

The single deliverable is `dune-results/dr_comparison_latest.xlsx` (a versioned
copy also lands in `dune-results/comparison/<timestamp>/dr_comparison_2026.xlsx`).

## Standard run

```bash
DUNE_API_KEY=<key> npm run regenerate
```

This re-executes the 9 monthly DR queries plus the Base PSM3 window(s)
overlapping the settlement month on Dune's large engine (~10–30 min, polled
automatically), refreshes `spark-dr-data/query_5650519_full.csv` from Spark's
public query, then runs combine + compare.

Variants:
- `npm run regenerate -- --no-execute` — skip Dune re-execution, reuse the
  latest stored results (free; use when the queries were already run).
- `npm run regenerate -- --no-spark-refresh` — keep the local Spark CSV.

## Before running, check

1. **Settlement cutoff** — `SETTLE_MONTH` in `src/scripts/settlement.ts`
   decides the last month included everywhere. To move it:
   ```bash
   npx tsx src/scripts/set-settlement-cutoff.ts <YYYY-MM>
   DUNE_API_KEY=<key> npx tsx src/scripts/update-dune-queries.ts        # push component + monthly SQL
   DUNE_API_KEY=<key> npx tsx src/scripts/update-psm3-base-windows.ts --no-execute  # push windowed set
   ```
   then regenerate. Only settle a month AFTER it has fully elapsed.
   **Base PSM3 window coverage**: the quarterly window set currently ends at
   2026-07-01. To settle 2026-07 or later, FIRST append a new window to the
   three tables (`WINDOWS` in `update-psm3-base-windows.ts` and
   `regenerate-dr-comparison.ts`, `SOURCES` in `combine-dr-results.ts`) and
   create the query on Dune (`create-psm3-base-windows.ts` shows how) —
   regenerate and combine both abort with an explicit error if coverage is
   missing.
2. **API key account** — the pipeline queries are owned by the **openmsc** Dune
   account (recreated 2026-07-03 via `recreate-dune-pipeline.ts`). Pushing SQL
   edits requires an openmsc key; executing/fetching works with any key.
3. **Other reference CSVs** (only if the user wants those tabs current):
   Amatsu months are hardcoded in `compare-dr.ts` (`TARGET`), and the payouts
   CSV path points at a dated snapshot — both need new source files from the
   partners, they are not Dune queries.

## Failure modes

- Execution failures list the failing query IDs — inspect on
  `https://dune.com/queries/<id>`; Base PSM3 windows time out if run on engines
  smaller than `large`.
- `combine` fetches whatever result is stored: if a monthly query was never
  executed after a SQL change, its stale result is silently used. When in
  doubt, run without `--no-execute`.
