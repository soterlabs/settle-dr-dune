# Foundational DR TWA Queries

Self-owned Dune queries that reproduce Spark's opaque per-user **time-weighted average balance (TWA)** datasets — the foundation of every Distribution Rewards (DR) revenue figure — and extend coverage to the chains/assets Amatsu tracks that Dune does not.

Each query reads raw on-chain events and outputs **per-user daily time-weighted balances with an attributed `ref_code`**. This is the combined Layer 0 + Layer 1 unit from the recommended structure in [`dr-query-analysis-v2.md`](../dr-query-analysis-v2.md) §11.2. These outputs are drop-in replacements for the Spark datasets consumed by `query_5310067` / `query_5358290` / `query_5358295` and feed the eventual Layer 2-4 stack (aggregation -> rate application -> USD conversion -> monthly rollup) unchanged.

## Methodology (fixed)

- **Reference implementation:** [`raw-queries/query_5358161.txt`](../raw-queries/query_5358161.txt) (self-owned stUSDS TWA). Its TWA machinery is reused verbatim as a shared tail: `running_balances` (transfer-driven) -> `daily_end_balances` / `daily_start_balances` -> `daily_referral_segments` -> `complete_user_dates` -> `complete_daily_balances`.
- **Attribution:** last-referral-wins. The active `ref_code` is forward-filled per user with `last_value(ref_code) ignore nulls over (... rows unbounded preceding)`, and a deposit/swap re-tags the user's entire running balance from that event forward. Confirmed as the deployed convention in [`dr-settlement-handover-review.md`](../dr-settlement-handover-review.md) §2.1. FIFO is out of scope.
- **Untagged sentinel:** untagged balances keep the raw `-999999` sentinel here. The `-999999 -> 99` (sUSDS) / `-> 127` (sUSDC) reclassification stays in the downstream aggregator, mirroring `query_5310067`.
- **TWA formula:** `time_weighted_avg_balance = segment_balance_time_product / 86400` (per-day segment products), identical to Spark.

## Shared output schema

Every query emits exactly these columns (matching `dune.sparkdotfi.result_spark_s_usds_s_usdc_time_weighted_average_balance`):

| Column | Notes |
|---|---|
| `blockchain` | `ethereum`, `base`, `arbitrum`, `optimism`, `unichain`, `avalanche_c` |
| `contract_address` | The tracked contract (vault / token / farm / PSM3) |
| `symbol` | Token symbol (`sUSDS`, `sUSDC`, `stUSDS`, `spUSDC`, ...) |
| `user_addr` | Depositor wallet |
| `dt` | Calendar day |
| `ref_code` | Attributed referral code; `-999999` = untagged |
| `time_weighted_avg_balance` | Daily TWA in token units |
| `day_type` | `transaction_day` or `no_transaction_day` |
| `segment_duration_seconds` | Present on `transaction_day` rows |
| `segment_balance_time_product` | Present on `transaction_day` rows |

## Templates -> file map

| File | Template | Mechanism | Targets |
|---|---|---|---|
| [`twa_susds_susdc_erc4626.sql`](twa_susds_susdc_erc4626.sql) | A | ERC4626 `Transfer` (balance) + separate `Referral` event matched by tx_hash (ref_code) | sUSDS eth; sUSDC eth/base/arbitrum/optimism/unichain |
| [`twa_stusds.sql`](twa_stusds.sql) | B | Same as A (this *is* the reference) | stUSDS eth |
| [`twa_susds_psm3_l2.sql`](twa_susds_psm3_l2.sql) | C | L2 sUSDS `Transfer` (balance) + PSM3 `Swap.referralCode` matched by tx_hash (ref_code) | sUSDS base/arbitrum/optimism/unichain |
| [`twa_usds_staking_farms.sql`](twa_usds_staking_farms.sql) | D | SNX `StakingRewards`: `Staked`/`Withdrawn` (balance) + `Referral` event (ref_code) | USDS Sky Farm / Spk Farm / Chronicle (eth) |
| [`twa_sp_vaults.sql`](twa_sp_vaults.sql) | E | Same as A; deployment ratio applied downstream | spUSDC eth+avalanche, spUSDT eth, spPYUSD eth, spETH eth |

**Correction vs. the original plan note:** Templates A and B are the *same* structure. Per the official Spark docs, sUSDS/sUSDC do not embed the referral in the ERC4626 `Deposit` event; they emit a separate `Referral(uint16 indexed referral, address indexed owner, uint256 assets, uint256 shares)` event — exactly like stUSDS. So the `ref_code` is sourced from a `*_evt_referral` table matched on `tx_hash` + `owner`, not from a deposit parameter.

## Revenue layers (2–4): from balances to USD DR revenue

The five `twa_*.sql` queries above are Layer 0/1 (per-user daily TWA **balance** + `ref_code`). The files below turn those into **DR revenue in USD**, mirroring Spark's daily-rewards engine (`raw-queries/xr-ar-rewards-daily-raw.txt`) but built entirely on our self-owned inputs — **no dependency on Spark's opaque `dune.sparkdotfi.result_spark_*` datasets.** Every rate/conversion is reproduced from a *transparent* Spark query (public decoded tables + hardcoded values only).

| File | Layer | What | Reproduces |
|---|---|---|---|
| [`rates_dr.sql`](rates_dr.sql) | 3a | XR/XR\*/XR-stUSDS reward rate by token-class + date | `query_5353955` (`static_rewards`) |
| [`conversion_susds.sql`](conversion_susds.sql) | 3b | sUSDS share→USDS daily rate (also used for sUSDC) | `query_5752873` |
| [`conversion_stusds.sql`](conversion_stusds.sql) | 3b | stUSDS share→USDS daily rate | `query_5449435` |
| [`conversion_sp_vaults.sql`](conversion_sp_vaults.sql) | 3b | sp\* share→USD (spETH × WETH price) | `query_5357785` |
| [`deployment_ratio_sp.sql`](deployment_ratio_sp.sql) | 3b | sp\* vault deployment ratio `(deployed/total)` per day | `query_6398769` + `query_6619793` |
| [`dr_rewards_monthly_susds_susdc.sql`](dr_rewards_monthly_susds_susdc.sql) | 2+3 | sUSDS/sUSDC → monthly `dr_usd` | `xr-ar-rewards-daily-raw.txt` |
| [`dr_rewards_monthly_psm3_base.sql`](dr_rewards_monthly_psm3_base.sql) | 2+3 | L2 sUSDS (PSM3, Base) → monthly `dr_usd` — **windowed** template, deployed as a set of public quarterly queries (7684981–7684988) | " |
| [`dr_rewards_monthly_psm3_arbitrum.sql`](dr_rewards_monthly_psm3_arbitrum.sql) | 2+3 | L2 sUSDS (PSM3, Arbitrum) → monthly `dr_usd` | " |
| [`dr_rewards_monthly_psm3_optimism.sql`](dr_rewards_monthly_psm3_optimism.sql) | 2+3 | L2 sUSDS (PSM3, Optimism) → monthly `dr_usd` | " |
| [`dr_rewards_monthly_psm3_unichain.sql`](dr_rewards_monthly_psm3_unichain.sql) | 2+3 | L2 sUSDS (PSM3, Unichain) → monthly `dr_usd` | " |
| [`dr_rewards_monthly_stusds.sql`](dr_rewards_monthly_stusds.sql) | 2+3 | stUSDS → monthly `dr_usd` | " |
| [`dr_rewards_monthly_farms.sql`](dr_rewards_monthly_farms.sql) | 2+3 | USDS farms → monthly `dr_usd` | " |
| [`dr_rewards_monthly_sp.sql`](dr_rewards_monthly_sp.sql) | 2+3 | sp\* vaults → monthly `dr_usd` | " |

### Why five per-source queries instead of one combined query

Dune's `query_<id>` reference is a **view**: it inlines the referenced query's SQL
and re-executes it. A single query that combined all five sources (the old
`dr_rewards_daily` / `dr_rewards_rollup`) therefore inlined all five heavy
foundational TWA queries at once and failed with **"this query has too many
stages and is too complex to execute."** Materialized views would fix this but
require a paid plan + cron scheduling.

The fix: one **per-source** query each referencing **exactly one** foundational
query, aggregated to **monthly** grain `(month, blockchain, token, ref_code)`.
Each stays under the stage limit, and the monthly output is only a few thousand
rows (the per-user-per-day explosion is collapsed by the `GROUP BY` before any
result is returned — so the foundational queries never need to be run or stored
on their own). The cross-asset, per-`ref_code` rollup is then merged from the
five small outputs **client-side** via
[`src/scripts/combine-dr-results.ts`](../src/scripts/combine-dr-results.ts)
(the old `dr_rewards_rollup` is retired/archived).

**Reward rate is per-(token-class, date), NOT per-ref_code.** `rates_dr.sql` keys only on the token's reward code (`XR` for sUSDS/USDS farms, `XR*` for sUSDC/sp\*, `XR-stUSDS` for stUSDS) and the date. The Spark/non-Spark "referral_type" split is a *display label only* and does not change the rate. This corrects the code-tier approximation in `src/scripts/reconstruct-128/rates.ts`, and means the stUSDS payout drop from 2026 is simply the standard `XR-stUSDS` 0.1%-APY rate (not a per-code DB override).

### Known placeholders / not-yet-implemented

- **sp\* deployment ratio** is now computed per-day from self-owned sources in `deployment_ratio_sp.sql` ([query 7683727](https://dune.com/queries/7683727)). No remaining placeholders — `dr_rewards_monthly_sp.sql` references it directly. Everything is now fully transparent end-to-end.

### Wiring (saved Dune query IDs)

The monthly queries reference the foundational + helper queries by Dune query ID
(the standard composition pattern). All are saved as public Dune queries.

**Foundational (Layer 0/1) + helpers (Layer 3) — referenced, not run directly:**

| File | Saved Dune query ID |
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
| `deployment_ratio_sp.sql` | [7683727](https://dune.com/queries/7683727) |

**Monthly revenue (Layer 2+3) — these are the ones you RUN:**

| File | Saved Dune query ID | References |
|---|---|---|
| `dr_rewards_monthly_susds_susdc.sql` | [7646377](https://dune.com/queries/7646377) | 7640317, 7640322, 7640323 |
| `dr_rewards_monthly_psm3_base.sql` | windowed set **7684981–7684988** (one per quarter) | **Windowed** template (`{{start_date}}`/`{{end_date}}`) deployed as 8 public quarterly queries; union = full coverage, replacing timed-out 7647196. See the table in the top-level README. inline + 7640322, 7640323 |
| `dr_rewards_monthly_psm3_arbitrum.sql` | [7647197](https://dune.com/queries/7647197) | inline + 7640322, 7640323 |
| `dr_rewards_monthly_psm3_optimism.sql` | [7647198](https://dune.com/queries/7647198) | inline + 7640322, 7640323 |
| `dr_rewards_monthly_psm3_unichain.sql` | [7647199](https://dune.com/queries/7647199) | inline + 7640322, 7640323 |
| `dr_rewards_monthly_stusds.sql` | [7646379](https://dune.com/queries/7646379) | 7640319, 7640322, 7640324 |
| `dr_rewards_monthly_farms.sql` | [7646380](https://dune.com/queries/7646380) | 7640320, 7640322 |
| `dr_rewards_monthly_sp.sql` | [7683760](https://dune.com/queries/7683760) | 7640321, 7640322, 7640325, 7683727 |

The cross-asset per-`ref_code` rollup is produced by
[`src/scripts/combine-dr-results.ts`](../src/scripts/combine-dr-results.ts), which
merges the five monthly outputs. The retired combined queries
`dr_rewards_daily` (7640326) and `dr_rewards_rollup` (7640327) are **archived** on
Dune (they hit the stage limit and are superseded).

> The `query_<id>` references use the **saved** version of each query, not your
> local `.sql` file. If you edit a foundational/helper `.sql` locally, push it to
> its saved query (e.g. via `src/scripts/save-dune-queries.ts` or the Dune UI)
> before re-running the monthly queries.

## Target matrix (from §6.1 of the settlement-handover review)

All decoded table names below were **verified against Dune** on 2026-06 via the
`<chain>.contracts` registry and `information_schema.tables` (see "How table names
were verified"). The event table is `<decoded table>_evt_<event>` (lowercased).

| Symbol | Chain | Tracked contract | Decimals | Template | Decoded table prefix |
|---|---|---|---|---|---|
| sUSDS | ethereum | `0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD` | 18 | A | `sky_ethereum.susds` |
| sUSDC | ethereum | `0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE` | 18 | A | `sky_ethereum.usdcvault` |
| sUSDC | base | `0x3128a0f7f0ea68e7b7c9b00afa7e41045828e858` | 18 | A | `sky_base.usdcvaultl2` |
| sUSDC | arbitrum | `0x940098b108fb7d0a7e374f6eded7760787464609` | 18 | A | `sky_arbitrum.usdcvaultl2` |
| sUSDC | optimism | `0xcf9326e24ebffbef22ce1050007a43a3c0b6db55` | 18 | A | `sky_optimism.usdcvaultl2` |
| sUSDC | unichain | `0x14d9143becc348920b68d123687045db49a016c6` | 18 | A | `sky_unichain.usdcvaultl2` |
| stUSDS | ethereum | `0x99CD4Ec3f88A45940936F469E4bB72A2A701EEB9` | 18 | B | `sky_ethereum.stusds` |
| sUSDS (PSM3) | base | PSM3 `0x1601843c5E9bC251A3272907010AFa41Fa18347E` / token `0x5875eEE11Cf8398102FdAd704C9E96607675467a` | 18 | C | `spark_protocol_base.psm3` (ref); `erc20_base.evt_Transfer` (bal) |
| sUSDS (PSM3) | optimism | PSM3 `0xe0F9978b907853F354d79188A3dEfbD41978af62` / token `0xb5B2dc7fd34C249F4be7fB1fCea07950784229e0` | 18 | C | `spark_protocol_optimism.psm3` (ref); `erc20_optimism.evt_Transfer` (bal) |
| sUSDS (PSM3) | arbitrum | PSM3 `0x2B05F8e1cACC6974fD79A673a341Fe1f58d27266` / token `0xdDb46999F8891663a8F2828d25298f70416d7610` | 18 | C | `spark_protocol_arbitrum.psm3` (ref); `erc20_arbitrum.evt_Transfer` (bal) |
| sUSDS (PSM3) | unichain | PSM3 `0x7b42Ed932f26509465F7cE3FAF76FfCe1275312f` / token `0xA06b10Db9F390990364A3984C04FaDf1c13691b5` | 18 | C | `spark_protocol_unichain.psm3` (ref); `erc20_unichain.evt_Transfer` (bal) |
| USDS (Sky Farm) | ethereum | `0x0650CAF159C5A49f711e8169D4336ECB9b950275` | 18 | D | `sky_ethereum.stakingrewards` |
| USDS (Spk Farm) | ethereum | `0x173e314C7635B45322cd8Cb14f44b312e079F3af` | 18 | D | `sky_ethereum.stakingrewards` |
| USDS (Chronicle) | ethereum | `0x10ab606b067c9c461d8893c47c7512472e19e2ce` | 18 | D | `sky_ethereum.stakingrewards` |
| spUSDC | ethereum | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | 6 | E | `spark_protocol_ethereum.sparkvault` |
| spUSDC | avalanche_c | `0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d` | 6 | E | `spark_protocol_avalanche_c.sparkvault` |
| spUSDT | ethereum | `0xe2e7a17dFf93280dec073C995595155283e3C372` | 6 | E | `spark_protocol_ethereum.sparkvault` |
| spPYUSD | ethereum | `0x80128DbB9f07b93DDE62A6daeadb69ED14a7D354` | 6 | E | `spark_protocol_ethereum.sparkvault` |
| spETH | ethereum | `0xfE6eb3b609a7C8352A241f7F3A21CEA4e9209B8f` | 18 | E | `spark_protocol_ethereum.sparkvault` |

> **Note on naming:** the three USDS farms and the four sp* vaults each share one
> bytecode, so Dune exposes a **single** decoded table per chain
> (`stakingrewards`, `sparkvault`) — rows are separated by `contract_address`.
> PSM3 also has a unified `spark_protocol_multichain.psm3_evt_swap` (carries a
> `blockchain` column) if you prefer one table over four.

## How table names were verified

Spark's own `raw-queries/` only reference a *few* decoded tables directly
(`sky_ethereum.stusds_evt_*`, `sky_ethereum.susds_evt_deposit/withdraw`,
`spark_protocol_ethereum.sparkvault_evt_deposit/withdraw`,
`spark_protocol_avalanche_c.sparkvault_evt_*`). Everything else — sUSDC, PSM3, the
staking farms — Spark only ever reads through **opaque pre-computed datasets**
(`dune.sparkdotfi.result_spark_*`), so their underlying decoded table names appear
*nowhere* in the raw queries. That is why they could not be copied from there.

The names were instead resolved directly from Dune's metadata using the API
(`src/scripts/discover-dune-tables.ts`), in two steps:

1. **Find the decoded namespace + contract name** from the per-chain decoded-contract
   registry. The pair `(namespace, name)` gives the table prefix `namespace_<chain>.<name>`:
   ```sql
   select 'ethereum' as chain, namespace, name, address from ethereum.contracts
   where address in (0xBc65ad17c5C0a2A4D159fa5a503f4992c7B545FE /* sUSDC */, ...)
   union all
   select 'base', namespace, name, address from base.contracts where address in (...)
   -- ...arbitrum/optimism/unichain similarly
   ```
2. **List the actual event tables** for those prefixes (confirms `referral`/`swap`/
   `staked`/`withdrawn`/`transfer` exist) from `information_schema.tables`:
   ```sql
   select table_schema, table_name from information_schema.tables
   where (table_schema = 'sky_ethereum' and table_name like 'usdcvault_evt_%')
      or (table_schema like 'spark_protocol_%' and table_name like 'psm3_evt_%')
      -- ...
   ```

This is the legitimate substitute for editor "autocomplete" (which only works inside
the Dune web SQL editor, not here). Re-run `discover-dune-tables.ts` to refresh.

If a contract were *not* decoded, the raw-logs fallback below applies — but in this
project every target is decoded, so no query uses it.

## Non-straightforward cases

1. **Raw-logs fallback** (works regardless of decoding). Decode events inline from `<chain>.logs` with `decode_evm_event(...)` or by filtering `topic0` and slicing `data`. Event signatures:
   - ERC20 `Transfer(address indexed from, address indexed to, uint256 value)` — topic0 `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef`
   - `Referral(uint16 indexed referral, address indexed owner, uint256 assets, uint256 shares)` — referral in `topic1`, owner in `topic2`
   - PSM3 `Swap(address indexed assetIn, address indexed assetOut, address sender, address indexed receiver, uint256 amountIn, uint256 amountOut, uint256 referralCode)` — `referralCode` is the last word of `data`
   - SNX `Staked(address indexed user, uint256 amount)` / `Withdrawn(address indexed user, uint256 amount)`

2. **PSM3 balance vs ref_code split (Template C).** sUSDS on L2s is *acquired* via PSM3 `Swap`, but a user's balance also changes via ordinary `Transfer`s afterwards. So balance is tracked from the L2 sUSDS token `Transfer` events, while the `ref_code` is sourced from the PSM3 `Swap.referralCode` (matched by `tx_hash` + `receiver`) and then forward-filled. A small number of Arbitrum rows carry malformed `referralCode`s (e.g. `123623963915635`, `90000000000000000000`) from bytes32 mis-parsing; these are negligible value and are filtered to `ref_code < 1e9`.

3. **Decimals (verified via `tokens.erc20`).** sUSDS/stUSDS/USDS = 18; **sUSDC = 18 on every chain** (eth + all L2s — *not* 6, despite USDC's 6); spUSDC/spUSDT/spPYUSD = 6; spETH = 18. Decimals live in each query's `token_targets` block.

4. **Staking farms (Template D).** Balance changes come from `Staked`/`Withdrawn` (underlying USDS amounts), not share `Transfer`s. Amatsu reclassifies untagged -> `127` for these farms (and all L2 flows), which differs from Dune's `99`/`127`-by-token split; this is left as raw `-999999` here and resolved downstream.

5. **sp* deployment ratio.** Amatsu applies a flat `0.9` allocation; Spark applies a per-day TWA deployment ratio (`query_6398769`). Neither is applied here — these queries emit the full balance. Deployment ratio is a downstream (Layer 2/3) concern.

## Out of scope (this iteration)

`referral_type` labeling (display only), FIFO, CoW Swap, monthly-payout formula specifics, and a *correct* sp\* deployment ratio (stubbed at `0.5` — see "Known placeholders"). Rate application, USD conversion, and the per-`ref_code` revenue rollup are now implemented (see "Revenue layers").

## Validation

Where a Spark equivalent exists (sUSDS/sUSDC eth+base+arbitrum, sp* eth+avalanche), run the new query alongside the opaque dataset for a recent month and diff per `(dt, ref_code)`. Divergences concentrated on multi-ref_code addresses confirm the attribution model is the differentiator. See [`validate_against_spark.sql`](validate_against_spark.sql).
