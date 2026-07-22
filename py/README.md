# DR pipeline on HyperSync (event-driven replacement for the Dune SQL)

This package reconstructs the Distribution-Rewards (DR) per-user **time-weighted
average balance (TWA)** — the foundation of every DR figure — directly from raw
on-chain **events pulled via Envio HyperSync**, replacing the Dune `twa_*.sql`
queries in [`../queries/`](../queries).

It is the first stage of migrating the whole pipeline off Dune. The TWA layer is
the hard, load-bearing part (dense window-function SQL); the rate / USD-conversion
/ monthly-rollup layers on top of it are comparatively simple and are the next
step (see **Status** below).

## Why / how it reuses settlement-cycle

The sibling repo `../settlement-cycle` already migrated its debt pipeline from
Dune to HyperSync. We reuse its proven approach:

- **`drhs/hypersync.py`** is adapted from `settlement-cycle`'s
  `src/settle/extract/hypersync.py` — the stateless HyperSync log-query client
  (paginated `/query`, block-timestamp binary search). Trimmed for a *batch*
  pipeline: because we pin to a fixed historical end block there is no reorg
  window to guard, so the Postgres reorg-safe store is dropped in favour of a
  plain on-disk cache of immutable block facts.
- The **transfer→balance reconstruction** pattern mirrors
  `settlement-cycle`'s `hypersync_balances.py` (sum signed `Transfer` legs,
  scale by decimals once).
- Auth uses the same free **`ENVIO_API_TOKEN`** (in the repo-root `.env`, copied
  from `../settlement-cycle/.env`).

## Architecture

```
drhs/
  hypersync.py            HyperSync HTTP client (query_logs, block resolution, cache)
  events.py               topic0 hashes + codecs (Transfer, Referral, PSM3 Swap, Staked/Withdrawn)
  twa.py                  the TWA engine — faithful port of the shared SQL tail
  sources/
    template_ab.py        Template A/B: ERC20/ERC4626 Transfer (balance) + Referral (ref_code)
run_source.py             CLI: run a source -> shared-schema CSV in ../hypersync-results/
validate.py               diff a source vs its Dune query per (chain, contract, user, dt, ref)
```

`twa.py` reproduces, step for step, the `queries/twa_stusds.sql` tail:
`running_balances → daily_end_balances → (transaction-day) daily_start_balances
→ intra-day segments → daily_referral_segments → complete_user_dates`
(no-transaction-day gap fill with forward-filled ref+balance) → `twab > 0`.
Attribution is **last-referral-wins**: a leg's `ref_code` is the referral named
for that user in the same tx (latest by log index), forward-filled per user.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install pandas requests python-dotenv
.venv/bin/python py/run_source.py stusds            # -> hypersync-results/twa_stusds.csv
.venv/bin/python py/validate.py stusds --end 2025-12-01
```

Output columns match `dune.sparkdotfi.result_spark_..._time_weighted_average_balance`
(the shared schema in `queries/README.md`): `blockchain, contract_address,
symbol, user_addr, dt, ref_code, time_weighted_avg_balance, day_type,
segment_duration_seconds, segment_balance_time_product`.

## Validation (stUSDS, Dune query 7877544)

**Per-row value parity — windowed (2024-09-01 → 2025-12-01)**, `validate.py stusds`:

| metric | value |
|---|---|
| matched (user, dt, ref_code) keys | 64,068 |
| within 1e-6 abs tolerance | **100.000%** |
| Σ TWA HyperSync vs Dune | 29,948,253,712.42494**6** vs …42494**50** (reldiff **1.3e-16**) |
| max abs diff | 2.2e-08 (on balances up to 6e7; float noise) |
| unmatched keys | 38 HS-only / 301 Dune-only, all TWA ≤ ~1e-12 (dust at the `twab>0` boundary) |

**Full history (through the 2026-07-01 cutoff)** — full per-row diff of all
columns (`validate.py stusds --end 2026-07-01 --dt-max 2026-07-01`):

| metric | value |
|---|---|
| matched (user, dt, ref_code) keys | 110,376 |
| TWA within 1e-6 abs tolerance | **100.000%** |
| `day_type` mismatches | **0 / 110,376** |
| `segment_duration_seconds` exact | **6,621 / 6,621** |
| Σ TWA HyperSync vs Dune | 40,123,619,568.04105**4** vs …04104**6** (reldiff **1.9e-16**) |
| rows: HyperSync vs Dune | 111,177 vs 112,407 |
| unmatched keys | 2,832 (801 HS-only + 2,031 Dune-only), **max \|TWA\| = 1.6e-08** |

Every one of the 2,832 unmatched rows is **dust** (TWA ≤ 1.6e-8): microbalance
rows on the `twab > 0` filter boundary, where float (HyperSync) vs double (Dune)
rounding flips inclusion — irreducible float non-associativity between two
engines, **not a logic difference** (`day_type` and `segment_duration` match
exactly, and Σ TWA agrees to 1.9e-16). The 2,031 − 801 = 1,230 net reconciles
with the 112,407 − 111,177 row-count gap.

**Bottom line:** the engine reproduces Dune's stUSDS output to floating-point
precision on every material balance, across full history; the only differences
are dust rows at the filter boundary, contributing ~nothing to any DR figure.

## Regression tests (offline Dune parity)

`py/tests/test_parity.py` locks in the Dune parity each token was validated at,
so a future change that breaks **any** already-ported token fails immediately.
It runs **offline** — no Dune quota, no network, ~3s:

```bash
.venv/bin/python -m pytest py/tests/test_parity.py -v
```

Each fixture under `py/tests/fixtures/<token>_<end>/` holds the **captured raw
HyperSync events** (Transfer + Referral `LogRow`s) plus the **Dune golden**
output for that window. The test replays the pipeline on the raw events and
asserts, per fixture: every shared `(chain, contract, user, dt, ref_code)` key
matches on TWA (abs tol 1e-6) and `day_type`; Σ TWA equals Dune (rel tol 1e-9);
and any unmatched keys are dust only (`|TWA| ≤ 1e-4`). A 0.01 % perturbation is
caught on every fixture.

### Per-token workflow (each new token follows this loop)

1. **Write** the source (event wiring) using HyperSync.
2. **Validate live** vs the token's Dune query: `py/validate.py <token> --query <id>`
   — expect exact Σ parity, only dust unmatched.
3. **Capture + commit** a fixture so it can't regress:
   `py/tests/capture_fixture.py <token> --end <date>`, then `pytest` (green).
4. Re-run the **whole** suite so earlier tokens are re-checked (step 3 does this).
5. Move on to the next token.

## Status

Per-token migration off Dune onto Envio HyperSync:

- [x] HyperSync client + event codecs + TWA engine (shared SQL tail)
- [x] **stUSDS (Template B): ported to Envio HyperSync — replaces Dune query
      7877544.** Full-history per-row parity confirmed (see above).
- [x] **sUSDS (Template A, eth): ported to Envio HyperSync — replaces the sUSDS/eth
      slice of Dune query 7877542.** Windowed per-row parity exact (Σ TWA reldiff 0;
      day_type + segment_duration exact; only sub-1.5e-11 dust rows differ).
- [x] **sUSDC (Template A, eth + base/arbitrum/optimism/unichain): ported —
      replaces the sUSDC slice of Dune 7877542.** Multi-chain launch-cohort
      windows validated exact (Σ TWA reldiff 0; day_type + segment_duration
      exact; only dust differs). Regression fixtures `susdc_mar` (eth/base/arb)
      and `susdc_jun` (optimism/unichain).
- [x] **L2 sUSDS via PSM3 (Template C): ported — vs Dune 7877543.** ref_code
      from PSM3 `Swap.referralCode` (assetOut=sUSDS filtered server-side via the
      indexed topic2), balance from token `Transfer`. **arbitrum / optimism /
      unichain validated + fixtured** (arbitrum Σ TWA reldiff 2.6e-10;
      day_type/segment exact). **base** uses the identical code path but its
      golden isn't captured — base PSM3 is pathologically high-volume (~90k
      transfers in 5 days; its fill tail blows the Dune datapoint quota on
      download), so it's covered by the shared code + the other three chains
      rather than its own fixture. NB: a self-transfer (from==to) splits into two
      legs with the same (block, log_index); Dune resolves that tie
      non-deterministically, so a handful of rows can differ immaterially — HS is
      arithmetically correct there. The parity test asserts materiality (tight Σ,
      negligible aggregate diff), not exact per-row equality Dune can't guarantee.
- [~] **USDS staking farms (Template D): coded + registered** (`usds_farms`;
      balance from `Staked`/`Withdrawn`, ref from the 3-arg StakingRewards
      `Referral`; topics verified on-chain). HS side runs; **Dune validation +
      fixture pending** (blocked on the Dune datapoint quota).
- [~] **sp\* vaults (Template E): coded + registered** (`sp_vaults`; == Template
      A code path, sp\* targets; SparkVault Referral topic0 verified on-chain).
      **Dune validation + fixture pending** (quota). Deployment ratio is Layer 3.
- [ ] Layer 2–4 stack on top of TWA: rate application (`rates_dr`), USD conversion
      (`conversion_*`), monthly rollups, and the per-ref_code combine
```
