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
  twa_*.sql                  Layer 0/1: per-user daily TWA balance + ref_code
  rates_dr.sql               Layer 3a: XR/XR*/XR-stUSDS reward rates
  conversion_*.sql           Layer 3b: share→USD conversion
  dr_rewards_monthly_*.sql   Layer 2+3: monthly DR revenue, one per source (RUN THESE)
  README.md                  Schema, wiring table, known placeholders

raw-queries/      Reference copies of Spark's original Dune queries
                  (read-only; used to verify our methodology)

src/scripts/      Helper TypeScript scripts
  save-dune-queries.ts     Deploy/re-deploy queries to Dune
  run-dune-query.ts        Execute a single SQL file via the Dune API
  combine-dr-results.ts    Merge the 5 monthly outputs into per-ref_code rollups

docs/             Background analysis and project documentation
```

---

## Saved Dune query IDs

**Run these five monthly queries** (each is self-contained; it auto-inlines the
foundational + helper queries it needs, so those never need to be run on their own):

| File | Dune ID |
|---|---|
| `dr_rewards_monthly_susds_susdc.sql` | [7646377](https://dune.com/queries/7646377) |
| `dr_rewards_monthly_psm3_base.sql` | [7647196](https://dune.com/queries/7647196) — DOES NOT RUN (times out); disabled in combine |
| `dr_rewards_monthly_psm3_arbitrum.sql` | [7647197](https://dune.com/queries/7647197) |
| `dr_rewards_monthly_psm3_optimism.sql` | [7647198](https://dune.com/queries/7647198) |
| `dr_rewards_monthly_psm3_unichain.sql` | [7647199](https://dune.com/queries/7647199) |
| `dr_rewards_monthly_stusds.sql` | [7646379](https://dune.com/queries/7646379) |
| `dr_rewards_monthly_farms.sql` | [7646380](https://dune.com/queries/7646380) |
| `dr_rewards_monthly_sp.sql` | [7646382](https://dune.com/queries/7646382) |

Referenced (do **not** run directly): foundational `twa_*` = 7640317–7640321;
helpers `rates_dr`/`conversion_*` = 7640322–7640325. See `queries/README.md` for
the full wiring table.

### How to run everything

1. Run the **five monthly queries** above on Dune (any order, independent). Each
   recomputes its one foundational query inline and aggregates to monthly, so the
   result is only a few thousand rows. Full history (from 2024-09-01) runs by
   default — no parameter needed.
2. Merge them into the cross-asset per-`ref_code` rollup locally:
   ```bash
   DUNE_API_KEY=<key> npm run combine
   ```
   Writes `dr_monthly_combined.csv`, `dr_rollup_by_refcode.csv`, and
   `dr_rollup_by_refcode_token.csv` to `dune-results/`.

The retired combined queries (7640326 daily, 7640327 rollup) are archived — a
single query combining all five sources exceeds Dune's stage limit (see
`queries/README.md`).

---

## Known gap: sp* deployment ratio

`dr_rewards_monthly_sp.sql` hardcodes `sp_deployment_ratio = 0.5` — this is
**deliberately wrong** to make sp* revenue figures obviously incorrect.
Spark's real value is a per-day TWA computed from an opaque internal dataset
(`query_6398769`); Amatsu used a flat `0.9`. Neither is reproduced here yet.
All other tokens (sUSDS, sUSDC, stUSDS, USDS farms) are fully transparent.

## Known gap: Base L2 sUSDS (PSM3) is missing

`dr_rewards_monthly_psm3_base.sql` (7647196) **always hits Dune's 30-minute
execution limit** and never returns a result, so it is **disabled** in
`combine-dr-results.ts`. Base L2 sUSDS DR revenue is therefore **undercounted**
in the combined rollup. The other three PSM3 chains (Arbitrum/Optimism/Unichain)
run fine. To fix, split Base further (by year/quarter via `{{end_date}}`) and
union client-side, or pre-materialize on a paid plan.

---

## Environment

```
DUNE_API_KEY=<key that owns query IDs 7640317–7640327>
```
