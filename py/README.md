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
validate_stusds.py        diff a source vs its Dune query per (user, dt, ref_code)
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
.venv/bin/python py/validate_stusds.py --end 2025-12-01
```

Output columns match `dune.sparkdotfi.result_spark_..._time_weighted_average_balance`
(the shared schema in `queries/README.md`): `blockchain, contract_address,
symbol, user_addr, dt, ref_code, time_weighted_avg_balance, day_type,
segment_duration_seconds, segment_balance_time_product`.

## Validation (stUSDS, Dune query 7877544)

For the 2024-09-01 → 2025-12-01 window (stUSDS live 2025-10-06):

| metric | value |
|---|---|
| matched (user, dt, ref_code) keys | 64,068 |
| within 1e-6 abs tolerance | **100.000%** |
| Σ TWA HyperSync vs Dune | 29,948,253,712.42494**6** vs …42494**50** (reldiff 1.3e-16) |
| max abs diff | 2.2e-08 (on balances up to 6e7; float noise) |
| unmatched keys | 38 HS-only / 301 Dune-only, all TWA ≤ ~1e-12 (dust at the `twab>0` boundary on the final day) |

The engine matches Dune to floating-point precision on all material balances.

## Status

- [x] HyperSync client + event codecs
- [x] TWA engine (shared SQL tail), validated on **stUSDS** (Template B)
- [x] Template A/B targets wired: sUSDS eth, sUSDC eth + base/arbitrum/optimism/unichain
      (same code path; per-target Dune validation pending — sUSDS/sUSDC are large)
- [ ] Template C (L2 sUSDS via PSM3 `Swap.referralCode` + token `Transfer` balance)
- [ ] Template D (USDS staking farms: `Staked`/`Withdrawn` + `Referral`)
- [ ] Template E (sp\* vaults; deployment ratio applied downstream)
- [ ] Rate application (`rates_dr`), USD conversion (`conversion_*`), monthly rollups,
      and the per-ref_code combine — the Layer 2–4 stack on top of TWA
```
