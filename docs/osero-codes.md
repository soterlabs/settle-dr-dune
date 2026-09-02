# Osero (codes 3000–3999): venue coverage and the 3006 gap

**Date:** 2026-09-02. Branch `osero-venues`. Companion to
[`osero-looping-vault.md`](osero-looping-vault.md) (3009, Morpho custody perimeter).

Osero's team lists six codes as active: **3000, 3002, 3006, 3007, 3008, 3009**.
This note records the on-chain census we ran to check that every one of them is
actually tracked, what we found, and the fix for the one that was not.

## Census (every tracked contract, events since 2026-01-01)

Scanned: `Referral` on sUSDS-eth, stUSDS, sUSDC ×5 chains, sp\* ×5; farm
`Referral` on the three USDS farms; PSM3 `Swap` (assetOut = sUSDS) on base /
arbitrum / optimism / unichain. Every 3xxx code that appears anywhere:

| code | events | owners | first → last | where | on Osero's list | in settled outputs |
|---|---|---|---|---|---|---|
| 3000 | 879 | 559 | 2026-04-15 → live | sUSDS-eth; PSM3 base/arb/opt/uni | yes | yes — $486 sUSDS-eth + ~$16 L2 PSM3 through Aug |
| 3002 | 11 | 3 | 2026-07-10 → 2026-08-20 | sUSDS-eth | yes | yes (dust) |
| **3006** | **67** | **3** | 2026-06-30 → 2026-08-21 | **sUSDS-eth; sUSDC base/arb/opt** | yes | **no — $0 anywhere** |
| 3007 | 5 | 5 | 2026-06-30 → 2026-07-14 | sUSDS-eth, stUSDS | yes | yes (dust) |
| 3008 | 7 | 3 | 2026-06-19 → 2026-08-05 | sUSDS-eth | yes | yes (dust) |
| 3009 | 45 | 3 | 2026-08-09 → live | sUSDS-eth (Morpho perimeter) | yes | yes — $21 Aug |
| 3001 | 7 | 1 | 2026-05-28 → 2026-08-14 | PSM3 base/opt | no | yes (sub-cent) |
| 3123 | 1 | 1 | 2026-08-21 | PSM3 base | no | yes ($0.0007) |
| 3333 | 13,890 | 8 | (≤2025-03) → 2026-07-28 | PSM3 base/arb | no | yes (~$11 total, $0.14 Aug) |

3002 / 3007 / 3008 owners all **retain** their shares in the referral tx
(net > 0), so they are ordinary holders and are tracked correctly — their DR is
small because their balances are small. 3001 / 3123 / 3333 are PSM3
`Swap.referralCode` values, a free-text field anyone can fill; 3333 has been in
use since March 2025 (a year before Osero existed) at bot-like frequency, so
none of the three is Osero's. Whether they are payable is a registry question
(`NON_PAYABLE_CODES`), not an attribution one; nothing here changes them.

## 3006: Osero via Jumper Earn (Li.Fi) — real code, stuck on a forwarder

All 67 `Referral(3006)` events are owned by **contracts that keep nothing**:

| owner | chains | events | net position in the referral tx |
|---|---|---|---|
| `0xe69b860fb5f12552b9c7675966ef9522fb734232` | ethereum, base, arbitrum, optimism (same address) | 7 + 22 + 22 + 14 = 65 | net ≤ 0 in **65 / 65** |
| `0x847e5ff14e18cde8fc289839bc7c747b922159bf` | base | 1 | net ≤ 0 (Li.Fi cross-chain destination via Receiver `0x4dac9d17…`) |
| `0x5b1628f8f1a015ff8b86808d1089e056989dfa4b` | ethereum | 1 | net ≤ 0 (2026-06-30, called directly — a deployer test deposit) |

Classifying the 65 txs by their Li.Fi events: every one ends in
`LiFiGenericSwapCompleted` with integrator **`jumper.exchange`** (the 7 on
ethereum sUSDS) or **`jumper.exchange.earn`** (the 58 on sUSDC L2s). So
`0xe69b…` is Jumper Earn's Sky deposit step: it calls the ERC-4626 `deposit`
with referral 3006, receives the minted shares, hands them to the
**LiFiDiamond** (`0x1231deb6…`), and the Diamond delivers them to the user —
a **two-hop** forward (`adapter → Diamond → user`). Sample txs: ethereum
`0x837eb644…` (WBTC → USDS → 9.19 sUSDS), base `0xce62ffd4…` (USDC → 1,899.94
sUSDC).

This is the Paraswap-1004 / 1inch-4011 shape of
[`adding-an-aggregator.md`](adding-an-aggregator.md) §B: the code fires on an
intermediary, the shares reach the user as a plain transfer, and under
last-referral-wins the tag sticks to a net-zero address and earns nothing.

### Fix (this branch)

1. **`3006` added to `template_ab.REROUTED_CODES`** — the code is re-attached to
   the net-positive end recipients of the owner's forward.
2. **Hop-following, opt-in per code** (`template_ab.REROUTE_FOLLOW_HOPS =
   {3006}`): the direct rule only re-routes along an owner → recipient edge
   and treats a net-zero recipient as a hop that is *not* tagged — which for
   Jumper's two-hop shape re-routes nothing. For codes in `REROUTE_FOLLOW_HOPS`
   the walk continues through net-zero forwarders to the first net-positive
   recipients. **1004 / 4011 are deliberately not in the set**: the settled
   months were paid under the direct rule and stay byte-identical
   (`test_two_hop_not_followed_for_direct_rule_codes`).
3. **Re-routing wired onto the sUSDC sources** (`susdc`, `susdc_mar`,
   `susdc_jun`) — until now only `susds_eth` carried `reroute=`, and 58 of the
   65 events are on sUSDC.

Precedence is unchanged: real user `Referral` > re-routed code > delivery
pseudo-tag; a later attribution signal ends the tag.

Known, accepted gaps: the two single-event owners above sit below
`MIN_INTERMEDIARY_EVENTS = 3` and are not re-routed (2 deposits). If either
address recurs it crosses the threshold retroactively on the next run.

### Impact (measured — full pipeline regeneration)

Authoritative figure: the **full chunked pipeline re-run on this branch**
(2026-09-02, warm log cache, 29 chunks) diffed row-by-row against the committed
August outputs — `hypersync-results/measurements/aug_regen_diff.csv`. 23 of
2,372 rows changed, all in the `susds_susdc` family; every other chunk (PSM3
incl. the 8 base shards, sp\*, farms, stUSDS, class-D holders) is byte-identical.
Net delta across all codes = **$0.000000000**.

| month | 3006 (USD) | from |
|---|---|---|
| 2026-07 | **0.7712** | 99 untagged (sUSDS-eth) |
| 2026-08 | **0.6568** | 99 untagged (sUSDS-eth) |
| total | **1.4282** | + $0.0002 on sUSDC L2s from 127/128/1017 |

Why lower than a standalone estimate: the largest 3006 wallet
(`0x7fb4991e…`, 16.7k sUSDS mid-July) took a **CowSwap delivery on
2026-07-21**, and under last-referral-wins its whole balance is 1003 from
07-22 on. A with/without measurement that runs the 3006 re-route *without*
the CowSwap program keeps that wallet on 3006 and reports $5.20 — the
number an earlier draft of this note carried. The re-route itself is
identical in both (7 tags, 6 wallets); only the competing program differs.
Lesson recorded in `adding-an-aggregator.md`: measure with the production
program set, or better, diff a full regeneration.

The 44 sUSDC-L2 wallets (`jumper.exchange.earn`) contribute ≈ $0.0002 in
total despite 58 deposits — they are **round trips**: the largest, 1,899.94
sUSDC on base (`0xce62ffd4…`, 2026-07-16 19:55), was redeemed at 19:56. A
one-minute holding earns nothing under a time-weighted balance; the code is
attributed correctly for whatever is held.

## Open items for ops

- Confirm 3006 is Osero's Jumper Earn integration code (the events are on
  Jumper's frontend, not Osero's). If 3006 belongs to someone else, re-routing
  it still attributes correctly — it just is not Osero's revenue.
- Payability of 3001 / 3123 / 3333 (not Osero's; PSM3 free-text codes).
