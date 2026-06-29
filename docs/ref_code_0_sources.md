# How `ref_code = 0` appears in the DR pipeline

This documents **every distinct way a wallet can end up tagged with `ref_code = 0`**
in the queries that actually feed the final DR numbers (the sources listed in
`src/scripts/combine-dr-results.ts`), the exact query/logic responsible, and a
categorization of the sources.

> **Status (2026-06):** the per-transaction aggregator fallback that used to be a
> second source of `0` (old "Category B") has been **removed** from the only query
> that had it (`twa_susds_susdc_erc4626.sql`). `0` is produced by exactly two
> mechanisms: a direct on-chain `Referral(referral = 0)` (Category A) and the
> PSM3 L2 `Swap.referralCode = 0` default (Category C).
>
> **Category C is now relabeled away from `0`** in the monthly PSM3 queries: L2
> sUSDS default-0 is split into `10001` (smart-contract holders) and `10000`
> (default PSM3 swaps) — see the Category C section below. So in the *final*
> combined output, `ref_code 0` for sUSDS is Category A (Ethereum) only.

## TL;DR — `0` is always a *real* on-chain zero

`ref_code = 0` is **never** a sentinel or a synthetic/derived value. It only ever
comes from an actual `0` carried by an on-chain referral mechanism. Specifically:

- **Untagged** balances use the sentinel `-999999`, which is later remapped to
  `99` (sUSDS / PSM3 sUSDS) or `127` (sUSDC), or kept as `-999999`
  (farms / stUSDS / sp). **Untagged is never remapped to `0`.**
- The standalone synthetic codes (`1003` CowSwap, `1004` Paraswap, `4011`,
  `9001` Aave USDS) are chosen specifically to avoid colliding with `0` (or any
  real code).
- `combine-dr-results.ts` / `compare-dr.ts` do **not** synthesize `0`; they only
  *split* `0` per token for display (`SPLIT_BY_TOKEN_CODES = {0, 1}`).

So if a wallet shows up as `ref_code = 0`, some contract emitted a referral value
of `0` for it. The question this enables is whether any of those `0`s are
*mistaken* attributions (e.g. an aggregator that defaults its `referralCode`
argument to `0`). With the tx-level fallback now removed (see below), the
remaining `0`s come only from a wallet's own `Referral`/`Swap` event.

---

## The two ways `0` is produced (current pipeline)

### Category A — explicit on-chain `Referral(referral = 0)`

A vault / farm / token contract emits a dedicated `Referral` event whose
`referral` field is `0`. The depositing wallet (the event's `owner`, or `user`
for the staking farms) is tagged `0`, then **forward-filled last-wins** until a
later referral event for the same `(contract, wallet)` overrides it.

This is the common path and exists for every non-PSM3 source:

| Token(s) | Referral event table | Tagged wallet | TWA query | Monthly query |
|---|---|---|---|---|
| sUSDS (eth) | `sky_ethereum.susds_evt_referral` | `owner` | `twa_susds_susdc_erc4626.sql` (query_7640317) | `dr_rewards_monthly_susds_susdc.sql` (7646377) |
| sUSDC (eth) | `sky_ethereum.usdcvault_evt_referral` | `owner` | same | same |
| sUSDC (base/arb/op/uni) | `sky_<chain>.usdcvaultl2_evt_referral` | `owner` | same | same |
| stUSDS (eth) | `sky_ethereum.stusds_evt_referral` | `owner` | `twa_stusds.sql` (query_7640319) | `dr_rewards_monthly_stusds.sql` (7646379) |
| USDS-SKY / USDS-SPK / USDS-CLE (eth) | `sky_ethereum.stakingrewards_evt_referral` | `user` | `twa_usds_staking_farms.sql` (query_7640320) | `dr_rewards_monthly_farms.sql` (7646380) |
| spUSDC / spUSDT / spPYUSD / spETH (eth) + spUSDC (avax) | `spark_protocol_<chain>.sparkvault_evt_referral` | `owner` | `twa_sp_vaults.sql` (query_7640321) | `dr_rewards_monthly_sp.sql` (7683760) |

> Note: a frontend that passes `0` when the user has no referral will produce
> Category-A `0`s for genuine end users. These are not necessarily "wrong", but
> they are the population to eyeball for whales / contracts that shouldn't be
> earning referral-0 DR.

### Category C — PSM3 L2 `Swap.referralCode = 0` (default no-referral on L2)

On L2s, sUSDS is acquired via the PSM3 `Swap` event, which carries the reward
code inline (`referralCode`). `0` is the **default value emitted when no referral
is supplied**, so it behaves as the L2 equivalent of "untagged" and is a large,
mostly-legitimate population. The `receiver` is tagged `0` and forward-filled.

- Tables: `spark_protocol_<chain>.psm3_evt_swap` (base / arbitrum / optimism / unichain)
- TWA query: `twa_susds_psm3_l2.sql` → `dr_rewards_monthly_psm3_*`

> **SPLIT (2026-06):** the monthly PSM3 queries no longer keep Category C sUSDS
> under `0`. In the `balances` CTE, `ref_code = 0` for the sUSDS token is now
> split by `user_addr`:
>
> | New code | Meaning | Rule |
> |---|---|---|
> | `10001` | **Smart Contract-Held L2 sUSDS** | `user_addr` is a known protocol/vault contract (ALM / sUSDC vault / PSM3 / Morpho / Fluid / Compound / Parallel / ExtraX). Per-chain address lists live in each `dr_rewards_monthly_psm3_<chain>.sql` and are mirrored in the **L2 sUSDS Filtered Addresses** tab of `compare-dr.ts`. |
> | `10000` | **Default PSM3 Swap** | any other `referralCode = 0` swap (genuine no-referral end users). |
>
> `-999999 → 99` (untagged) is unchanged. After the split **no L2 sUSDS remains
> under `ref_code 0`** — the only `0` sUSDS left is Ethereum Category A. This
> applies to all four chains, INCLUDING Base (all 8 windowed queries
> `7684981–7684988` must be re-run after the template edit).

> **In the diagnostic query: arbitrum / optimism / unichain are INCLUDED; only
> Base is excluded.** Base's full-history per-user reconstruction is the one that
> times out (it is windowed into 8 quarterly queries in the pipeline for exactly
> this reason), so it is skipped. The other three L2s run as single queries
> upstream, so they are kept.

---

## REMOVED — old "Category B": aggregator / intermediary tx-level fallback `= 0`

> **This mechanism no longer exists in the pipeline.** It is documented here only
> so historical results and the diagnostic query's scope make sense.

`twa_susds_susdc_erc4626.sql` *used to* have a second attribution path,
`referral_per_tx_fallback`, that the other sources never had. On an **incoming**
sUSDS/sUSDC transfer, if the primary join (`tx + to = owner`) missed — which
happens when the `Referral` event's `owner` is an **intermediary** (e.g. a
Paraswap router) rather than the recipient — the recipient inherited the
**transaction's** last referral code:

```
-- OLD logic, now deleted:
coalesce(latest_referral_per_tx.ref_code, referral_per_tx_fallback.ref_code)
```

If that intermediary/aggregator supplied a `referralCode` of `0`, the downstream
recipient EOA was tagged `ref_code = 0` even though it never itself chose a
referral. This was the most likely place for **mistaken** `0` attribution.

**Current state:** the `referral_per_tx_fallback` CTE and all six
`coalesce(lr.ref_code, fb.ref_code)` joins were removed (see the bold comment
block at lines 114–133 of `twa_susds_susdc_erc4626.sql`). `ref_code` now comes
**only** from the direct `Referral` event matched by `(tx_hash, owner = the
transferring wallet)` via `latest_referral_per_tx`. Transfers whose `Referral`
`owner` is an intermediary now fall through to **untagged (`-999999`)** instead of
inheriting the tx's code. This removal is marked **TEMPORARY** in the query: a
corrected cross-event attribution may be reintroduced once the methodology is
rebuilt and its numbers analyzed.

> The fallback pattern still appears in some **diagnostic** queries
> (`diag_susds_ref1004_*`, `diag_susds_ref4011_*`) — those are investigative and
> do **not** feed `combine-dr-results.ts`, so they do not affect DR numbers.

---

## Contract-address exclusion list (affects which `0`s survive)

`twa_susds_susdc_erc4626.sql` (query_7640317) also carries an
`excluded_addresses` CTE (lines 41–51) whose entries are **dropped from the final
output entirely** via `and b.user_addr not in (select addr from excluded_addresses)`.
Because they never appear, they can never carry **any** ref_code — including `0`.
These are protocol/vault contracts that would otherwise double-count the
underlying user positions they custody:

| Address | Label |
|---|---|
| `0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe` | sUSDC vault (holds sUSDS for sUSDC depositors) |
| `0xbbbbbbbbbb9cc5e90e3b3af64bdaf62c37eeffcb` | Morpho |
| `0xBe3d4ec488A0a042BB86F9176C24f8CD54018BA7` | Pendle |
| `0x00836Fe54625BE242BcFA286207795405ca4fD10` | Curve PSM |

This exclusion currently lives only in the sUSDS/sUSDC foundational
(query_7640317). The other foundationals (stUSDS, farms, sp, PSM3 L2) do **not**
filter these addresses.

---

## Source categorization (by attribution shape)

| Shape | Sources | `0` categories present |
|---|---|---|
| ERC4626 vault + separate `Referral` event (Template A) | sUSDS, sUSDC (eth + L2) | A |
| ERC4626 Spark vault + `Referral` event (Template E) | spUSDC, spUSDT, spPYUSD, spETH | A |
| ERC4626 stUSDS + `Referral` event (Template B) | stUSDS | A |
| Synthetix staking farm + `Referral` wrapper (Template D) | USDS-SKY, USDS-SPK, USDS-CLE | A |
| PSM3 L2 swap, inline `referralCode` (Template C) | L2 sUSDS | C |

> All non-PSM3 sources now share the same attribution shape: balance from
> `Transfer`, ref_code from a direct `Referral` event matched by `(tx, owner)`.
> Template A no longer has its old tx-level fallback, so it is no longer special.

## What is *not* a source of `0`

- `-999999` (untagged) → `99` / `127` / kept — **never** `0`.
- Synthetic standalone queries: `1003`, `1004`, `4011`, `9001` — never `0`.
- The removed `referral_per_tx_fallback` — no longer produces anything.
- Excluded contract addresses (above) — dropped entirely, produce no rows.
- `combine-dr-results.ts` / `compare-dr.ts` — only split `0` per token for
  display; do not create it.

---

## Diagnostic query

`queries/diag_ref_code_0_sources_2026.sql` is deliberately **simple and
event-only**: it reads just the (tiny) referral/swap event tables and lists a few
example wallets per source that are **currently tagged `0`** (their latest
referral/swap event code is `0`, so they are tagged `0` in 2026). For each it
shows the source/token, the per-source count of `0`-tagged wallets, `became_0_at`
(+ a `became_0_in_2026` flag), the number of `0` events, and `is_eth_contract`
(whether the address is a deployed Ethereum contract — a strong signal of
mistaken attribution; protocols/routers should not be earning referral DR).

Scope and simplifications:

- **Covers Category A and Category C** — i.e. every `0` mechanism that still
  exists in the pipeline. The old tx-level fallback ("Category B") has been
  removed from the foundational query, so there is no longer a hidden `0` source
  for the diagnostic to miss.
- **No balances / DR are computed** — it is an existence + example-wallets check,
  not a magnitude ranking. (Earlier balance/DR versions hit the stage limit.)
- Covers all listed A/C sources **except PSM3 sUSDS on Base** (which times out);
  PSM3 sUSDS on arbitrum / optimism / unichain is included.
