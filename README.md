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

> **Ownership**: the whole pipeline was re-created under the **openmsc** Dune
> account on 2026-07-03 (`src/scripts/recreate-dune-pipeline.ts` prints the
> old→new ID mapping). The previous account's copies still exist on Dune but
> are no longer maintained.

**Run these five monthly queries** (each is self-contained; it auto-inlines the
foundational + helper queries it needs, so those never need to be run on their own):

| File | Dune ID |
|---|---|
| `dr_rewards_monthly_susds_susdc.sql` | [7877552](https://dune.com/queries/7877552) |
| `dr_rewards_monthly_psm3_base.sql` | windowed set [7877571–7877574, 7877576–7877579](#base-l2-susds-psm3--windowed-set) (one per quarter, 7877575 unused; supersedes timed-out 7647196) |
| `dr_rewards_monthly_psm3_arbitrum.sql` | [7877565](https://dune.com/queries/7877565) |
| `dr_rewards_monthly_psm3_optimism.sql` | [7877566](https://dune.com/queries/7877566) |
| `dr_rewards_monthly_psm3_unichain.sql` | [7877568](https://dune.com/queries/7877568) |
| `dr_rewards_monthly_stusds.sql` | [7877553](https://dune.com/queries/7877553) |
| `dr_rewards_monthly_farms.sql` | [7877554](https://dune.com/queries/7877554) |
| `dr_rewards_monthly_sp.sql` | [7877555](https://dune.com/queries/7877555) |

Referenced (do **not** run directly): foundational `twa_*` = 7877542–7877546;
helpers `rates_dr`/`conversion_*` = 7877547–7877550. See `queries/README.md` for
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

## sp* deployment ratio

`dr_rewards_monthly_sp.sql` uses a per-day deployment ratio from the
self-owned `deployment_ratio_sp.sql` ([query 7877551](https://dune.com/queries/7877551)),
which reproduces `query_6398769` + `query_6619793` with no dependency on any
opaque Spark dataset. All tokens (sUSDS, sUSDC, stUSDS, USDS farms, sp\*) are
now fully transparent end-to-end.

## Base L2 sUSDS (PSM3) — windowed set

The original `dr_rewards_monthly_psm3_base.sql` (query 7647196) **always hit
Dune's 30-minute execution limit** because the full-history per-user daily TWA
was too large for one execution. It is replaced by a **set of public, windowed
Dune queries** — one per calendar quarter — whose union reproduces the original's
full coverage. Each quarter only materializes one `[start, end)` slice and runs
on the `large` engine in ~1–15 min (see the SQL file header for why the split
stays equivalent to the un-windowed logic). `queries/dr_rewards_monthly_psm3_base.sql`
is the parameterized template each window is baked from.

| Quarter | Dune query |
|---|---|
| 2024-09-01 → 2024-12-01 | [7877571](https://dune.com/queries/7877571) |
| 2024-12-01 → 2025-03-01 | [7877572](https://dune.com/queries/7877572) |
| 2025-03-01 → 2025-06-01 | [7877573](https://dune.com/queries/7877573) |
| 2025-06-01 → 2025-09-01 | [7877574](https://dune.com/queries/7877574) |
| 2025-09-01 → 2025-12-01 | [7877576](https://dune.com/queries/7877576) |
| 2025-12-01 → 2026-03-01 | [7877577](https://dune.com/queries/7877577) |
| 2026-03-01 → 2026-06-01 | [7877578](https://dune.com/queries/7877578) |
| 2026-06-01 → 2026-07-01 | [7877579](https://dune.com/queries/7877579) |

All eight windows are wired into `combine-dr-results.ts` as `psm3_base` sources.
Re-run only the affected quarter(s) on Dune when fresh data is needed; the
combine script fetches stored results for free.

---

## Environment

```
DUNE_API_KEY=<key from the openmsc account, which owns the pipeline queries 7877542–7877579>
```
