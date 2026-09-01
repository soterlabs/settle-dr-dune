# Osero gtSkyLooping (code 3009): custody-perimeter DR tracking

**Date:** 2026-08-26 (chain time). Implements DR attribution for Osero's
Morpho looping strategy per the externally authored spec *"Tracking
Osero-Tagged sUSDS in Morpho"*, which we verified end-to-end on-chain before
implementing. Module: `py/drhs/sources/custody.py`; audit:
`py/verify_osero_custody.py`.

## The strategy, verified on-chain

`gtSkyLooping` (`0xeA40De595f099cA04695b0Ca105499E50AF77f92`) mints sUSDS
with Osero referral code **3009** and immediately supplies it to Morpho Blue
as collateral, borrowing USDT to loop. Facts established by replay:

- 14 `Referral(3009)` mints, **569,070.94 shares**, 2026-08-13 → 2026-08-26;
  no other code ever used by the strategy.
- ~136,849 shares redeemed during a partial unwind (Aug 16–19).
- The spec's §7 invariant (`event-replay collateral == Morpho.position()`)
  holds exactly, per market.
- **The spec's pinned market id went stale in 12 days**: the strategy fully
  exited `0x3274…0b` on Aug 25–26 and moved 432,190.80 sUSDS of collateral
  to market `0x26b178d4…4d96`. Our implementation therefore keys the
  position by `onBehalf == strategy` across ALL sUSDS-collateral Morpho
  markets (collateral token resolved from `CreateMarket` events) — market
  migrations are tracked automatically.
- Wallet residue: 57.75 sUSDS. Current perimeter total **H = 432,248.55**.

## How the implementation works (W + C by construction)

Morpho stays in `TEMPLATE_A_EXCLUDED` — its pooled balance is never
attributed to anyone. For a declared `CustodyPerimeter` only:

- the strategy's ordinary Transfer legs are untouched (W);
- each Morpho position event adds a C-leg: `+assets` (SupplyCollateral),
  `-assets` (WithdrawCollateral), `-seizedAssets` (Liquidate).

Tracked balance ≡ W + C with **no transfer/event matching**: a supply's
`-W` transfer and `+C` event share a block and cancel at zero duration
inside the TWA; an unmatched transfer to Morpho has no C-leg and stays a
disposal (spec §6.4); withdraw-to-other and external liquidations hit only C
(disposals); a self-liquidation's `-C` cancels the returning `+W` transfer.
The 3009 tag attaches through the ordinary (tx, owner) referral join and
forward-fills — no new attribution mechanism, no double counting (these
shares were previously attributed to no one).

Convention divergence from the spec, deliberate and flagged: our engine is
last-referral-wins whole-balance rather than the spec's `A = min(R, H)`
cap. Divergence observed to date: ~$25 of DEX dust. A third-party
`SupplyCollateral` onBehalf of the strategy (never observed) would inherit
the tag — a WARNING canary fires if one appears.

## Activation and economics

- The strategy started **after** the settled window (July 2026), so this
  feature changes **nothing** in any settled output; it activates with the
  August settlement (bump of `DEFAULT_END` to 2026-09-01).
- Scale, at the post-2026-07-09 XR rate (0.2%): ≈ **$4.40 accrued** since
  inception; ≈ **$77/month** run-rate at the current 432k shares.
- Pending ops sign-offs before payment: the perimeter declaration itself
  (this doc + `OSERO_GTSKYLOOPING` in code constitute the proposal) and an
  eligibility window for Osero's 3000-range codes.

## Audit

`py/verify_osero_custody.py` rebuilds the strategy's legs through the real
pipeline path and asserts tracked balance == live
`sUSDS.balanceOf(strategy) + Σ Morpho.position(market, strategy).collateral`
— run it whenever the perimeter or the strategy's behaviour is in question.
