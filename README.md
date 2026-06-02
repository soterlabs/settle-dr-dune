# DR Pipeline — Dune SQL Queries

Transparent on-chain reconstruction of **Distribution Rewards (DR)** revenue
for all referral codes, across all assets and chains supported by Sky/Spark.

Built as a modular, self-owned alternative to Spark's opaque
`dune.sparkdotfi.result_spark_*` datasets. Every reward rate and USD
conversion is reproduced from public Dune decoded tables or hardcoded values
only; the only known gap is the `sp*` deployment ratio (see below).

---

## Quick start

```bash
npm install
cp .env.example .env   # add DUNE_API_KEY=<your key>
```

The main deliverable is the SQL in `queries/`. See `queries/README.md` for
full schema, table-name verification notes, and the wiring table of saved
Dune query IDs.

---

## Repository layout

```
queries/          Core SQL — all Dune queries, fully documented
  twa_*.sql       Layer 0/1: per-user daily TWA balance + ref_code
  rates_dr.sql    Layer 3a: XR/XR*/XR-stUSDS reward rates
  conversion_*.sql Layer 3b: share→USD conversion
  dr_rewards_daily.sql   Layer 2+3: daily DR revenue (all ref_codes)
  dr_rewards_rollup.sql  Layer 4: all-time rollup per ref_code
  README.md       Schema, wiring table, known placeholders

raw-queries/      Reference copies of Spark's original Dune queries
                  (read-only; used to verify our methodology)

src/scripts/      Helper TypeScript scripts
  save-dune-queries.ts     Deploy/re-deploy all queries to Dune
  update-dune-date-params.ts  Patch end_date parameter on foundational queries
  run-dune-query.ts        Execute a single SQL file via the Dune API
  discover-dune-tables.ts  Probe Dune metadata for table/column discovery

docs/             Background analysis and project documentation
```

---

## Saved Dune query IDs

| File | Dune ID |
|---|---|
| `twa_susds_susdc_erc4626.sql` | [7640317](https://dune.com/queries/7640317) |
| `twa_susds_psm3_l2.sql` | [7640318](https://dune.com/queries/7640318) |
| `twa_stusds.sql` | [7640319](https://dune.com/queries/7640319) |
| `twa_usds_staking_farms.sql` | [7640320](https://dune.com/queries/7640320) |
| `twa_sp_vaults.sql` | [7640321](https://dune.com/queries/7640321) |
| `rates_dr.sql` | [7640322](https://dune.com/queries/7640322) |
| `conversion_susds.sql` | [7640323](https://dune.com/queries/7640323) |
| `conversion_stusds.sql` | [7640324](https://dune.com/queries/7640324) |
| `conversion_sp_vaults.sql` | [7640325](https://dune.com/queries/7640325) |
| `dr_rewards_daily.sql` | [7640326](https://dune.com/queries/7640326) |
| `dr_rewards_rollup.sql` | [7640327](https://dune.com/queries/7640327) |

Run order: foundational queries (7640317–7640321) and helpers
(7640322–7640325) first (independently), then 7640326, then 7640327.

The `{{end_date}}` parameter on foundational queries (default `2030-01-01`)
controls the scan cutoff and therefore cost. Set it to e.g.
`2025-09-01 00:00:00` for a cheaper one-year test run.

---

## Known gap: sp* deployment ratio

`dr_rewards_daily.sql` hardcodes `sp_deployment_ratio = 0.5` — this is
**deliberately wrong** to make sp* revenue figures obviously incorrect.
Spark's real value is a per-day TWA computed from an opaque internal dataset
(`query_6398769`); Amatsu used a flat `0.9`. Neither is reproduced here yet.
All other tokens (sUSDS, sUSDC, stUSDS, USDS farms) are fully transparent.

---

## Environment

```
DUNE_API_KEY=<key that owns query IDs 7640317–7640327>
```
