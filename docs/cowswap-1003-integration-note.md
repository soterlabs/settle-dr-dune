# Integration note: CowSwap synthetic ref_code 1003

**Status:** TODO — not yet integrated into the foundational TWA pipeline. The
standalone query `queries/diag_susds_cowswap_1003_monthly_dr.sql` exists only as
a **diagnostic** to size the 1003 magnitude for sUSDS + stUSDS on Ethereum.
This note records the double-counting risk that MUST be handled when 1003 is
folded into the real pipeline.

## The question

> When an existing ref_code user uses CowSwap, will their balance be counted
> under two ref_codes (double counted), or will the ref_code be overwritten
> (last-one-wins) as intended?

## How attribution works today

The foundational TWA queries (`twa_susds_susdc_erc4626.sql`, `twa_stusds.sql`,
`twa_susds_psm3_l2.sql`) attribute **each (chain, contract, user, day) to exactly
ONE `ref_code`**:

- Balance comes from ERC20 `Transfer` events.
- `ref_code` comes from a separate signal — a Sky `Referral` event (Templates
  A/B) or a PSM3 `Swap.referralCode` (Template C) — matched to the transfer by
  `tx_hash` (+ `owner`/`receiver`), then **forward-filled, last-referral-wins**
  (`last_value(ref_code) ignore nulls over (... rows unbounded preceding)`).
- A deposit/swap re-tags the user's **entire running balance** from that event
  forward. There is one attribution per user-day, never two.

**The catch for CowSwap:** the CowSwap settlement contract
(`0x9008d19f58aabd9ed0d60971565aa8510560ab41`) does **not** emit a Sky `Referral`
event and is not a PSM3 swap. So in the pipeline as it stands, a CowSwap-acquired
sUSDS/stUSDS balance carries **no ref_code signal** on that tx. The forward-fill
therefore keeps whatever the user had before:

- If the wallet previously held a real code (e.g. 4011): it **stays 4011**.
- If the wallet was never tagged: it **stays `-999999`** → reclassified
  downstream to `99` (sUSDS) / `127` (sUSDC).

So **the main pipeline does not know about 1003 at all** — those balances are
currently attributed elsewhere.

## The double-counting risk

The standalone 1003 query **independently recomputes the same wallets' same
balance-days** and attributes them to 1003. Therefore:

> ⚠️ If you simply `UNION ALL` / sum the standalone 1003 output on top of the
> main pipeline output, every CowSwap balance-day is counted **twice** — once
> under its main-pipeline code (prior real code or `99`/`127`) **and** once under
> 1003. DR for those days is doubled. This is the wrong behavior.

## Correct integration: inject synthetic 1003 Referral events

The desired behavior (and what the original Python snippet did via
`synthetic_referals`) is **last-one-wins overwrite within the single pipeline**,
not a parallel additive layer. Concretely:

1. In the foundational query, add a CTE that produces **synthetic referral rows**
   for every CowSwap deposit tx: one row per (tx_hash, final sUSDS/stUSDS
   recipient) with `ref_code = 1003`, carrying the tx's `evt_block_number` /
   `evt_index` so ordering is well-defined.
2. `UNION ALL` those synthetic rows into `raw_referral_events` **before**
   `latest_referral_per_tx` / `referral_per_tx_fallback`.
3. The existing forward-fill then does the rest: from the CowSwap tx forward, the
   user's running balance is tagged 1003 until/unless a later real referral event
   overwrites it again.

This guarantees:

- **Single attribution per user-day** — no double counting.
- **Last-wins honored** — a prior 4011 user who swaps via CowSwap becomes 1003
  from that tx forward; days *before* the swap remain 4011; a later real tagged
  deposit would overwrite 1003 again.
- Untagged CowSwap users move from `99`/`127` to 1003 (net-new attribution, not
  additive on top).

### Matching subtleties to verify at integration time

- **Recipient vs. intermediary.** The synthetic referral's owner must be the
  **final token recipient** (the `Transfer."to"`), so it matches the incoming
  transfer in `latest_referral_per_tx` (keyed by `tx_hash, user_addr, contract,
  blockchain`). If CowSwap routes through an intermediary, the
  `referral_per_tx_fallback` path (tx-level, no `user_addr`) is the safety net —
  confirm it still resolves to 1003 and not a stray code.
- **Ordering within a tx.** Give synthetic rows a sensible `evt_index` (e.g. the
  sUSDS Transfer's index) so that if a real referral and a synthetic 1003 ever
  coexist in one tx, last-by-`evt_index` resolves deterministically. In practice
  CowSwap txs have no real Sky referral event, so this is rare.
- **`-999999 → 99/127` reclassification.** Once 1003 is injected upstream, those
  balance-days are no longer `-999999`, so they correctly drop out of the
  `99`/`127` buckets in `dr_rewards_monthly_susds_susdc.sql`. Verify the
  untagged totals shrink by exactly the 1003 amount (good reconciliation check).

## Rate handling (already correct in the standalone)

- sUSDS → reward code **XR** (0.6% APY 2024–2025, **0.5%** 2026+).
- stUSDS → reward code **XR-stUSDS** (0.6% APY 2024–2025, **0.1%** 2026+).

`rates_dr.sql` keys the rate by **token class + date only** (not ref_code), so
re-tagging to 1003 does **not** change the rate a wallet earns — only which
bucket the DR is reported under. The XR vs XR-stUSDS split must stay driven by
token symbol, exactly as the standalone diagnostic already does.

## Validation checklist before trusting integrated 1003 numbers

- [ ] Confirm CowSwap txs carry no real Sky `Referral` event (so injection can't
      collide with a genuine code).
- [ ] Check whether any `wallets_cowswap` wallet held a prior real code; confirm
      pre-swap days keep the old code and only post-swap days flip to 1003.
- [ ] After injection, confirm untagged (`99`/`127`) totals drop by the 1003
      amount — net-zero shift, not additive.
- [ ] Multi-chain: replicate the CowSwap detection on each chain (settlement
      address is the same `0x9008...ab41` across chains) before extending beyond
      Ethereum.
