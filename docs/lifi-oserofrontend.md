# Li.Fi `oserofrontend` (code 3900): integrator-anchored aggregator program

**Date:** 2026-09-02. Branch `osero-venues`. Module `py/drhs/sources/lifi.py`;
program `lifi.OSERO_FRONTEND`, wired on `susds_eth`. Code **3900 is the
placeholder ops assigned** for the program; the eligibility start
(`2026-04-01`) is PROVISIONAL pending ops.

## Why a new shape

Osero's frontend routes swaps and bridges through Li.Fi. Li.Fi never puts a
Sky `Referral` on the end user: when its sUSDS deposit step fires one, the
code is Li.Fi's own (**4012**, observed on the adapter `0x9f12686c…` in the
cross-chain sample below) and the owner is the adapter, not the user. The
user's sUSDS lands as a plain `Transfer` out of a Li.Fi contract — the CowSwap
shape ([`adding-an-aggregator.md`](adding-an-aggregator.md) §A).

The difference from CowSwap: the LiFiDiamond is **multi-tenant**. Every
integrator's deliveries leave the same contracts (1.24M Diamond events on
ethereum in Mar–Sep 2026; `jumper.exchange`, `_binancewallet`, `base-app`,
`phantom`, …). Tagging "every delivery from the Diamond" would hand Osero other
integrators' users. So the program is **anchored to Li.Fi's own events**, which
carry the integrator id, and only deliveries inside those txs count.

## Anchors (verified on the two spec txs, 2026-07-30)

| flow | anchor event | where | integrator | tx to tag |
|---|---|---|---|---|
| same-chain swap | `LiFiGenericSwapCompleted(transactionId, integrator, referrer, receiver, from, to, amounts)` | LiFiDiamond `0x1231deb6…`, target chain | in the event | that tx |
| cross-chain | `LiFiTransferStarted(BridgeData{transactionId, bridge, integrator, …, destinationChainId, …})` | LiFiDiamond, **origin** chain | in the event | — (origin) |
| ↳ destination | `LiFiTransferCompleted(transactionId indexed, …)` (Executor / Receiver) and `AssetSwapped(transactionId, …)` (Executor `0xd9b2da9c…`) | target chain | — (joined on `transactionId`) | that tx |

Sample: base `0xfadc262b…` (`LiFiTransferStarted`, integrator `oserofrontend`,
bridge `stargateV2`, dest 1, id `0xea31aac2…`) → ethereum `0x1edf4214…`
(USDC → … → USDS → sUSDS via adapter `0x9f12686c…` with `Referral(4012)` on the
adapter; `AssetSwapped` ×2 and `LiFiTransferCompleted` with the same id;
0.90 sUSDS delivered Executor → user `0x21e7105b…`).

## Mechanism

`lifi.IntegratorProgram(name, ref_code, integrator, origin_chains, start, end)`
resolves per target at fetch time (`template_ab._legs_for_target`) into a plain
`SyntheticProgram` with a **`txs` restriction**:

1. scan the target chain's Diamond `LiFiGenericSwapCompleted`, keep rows whose
   integrator matches (case-insensitive) → same-chain txs;
2. scan every origin chain's Diamond `LiFiTransferStarted`, keep rows whose
   integrator matches **and** `destinationChainId` is the target → ids;
3. on the target chain, `LiFiTransferCompleted` with `topic1 ∈ ids`
   (server-side filter, any emitter) plus the Executor's `AssetSwapped` with a
   matching id → destination txs;
4. delivery contracts = Diamond + Executor + every Li.Fi contract that emitted
   in an anchored tx (Receiver variants).

`synthetic_referrals` then applies the unchanged rules — received the token
FROM a program contract, net-positive across the tx, inside the eligibility
window — **but only in anchored txs**. Precedence (real Referral > re-routed
code > pseudo-tag), last-wins termination and the exclusion guard are untouched.
`txs=None` (CowSwap) is byte-identical to before.

Cost: the Diamond cannot be filtered on the integrator server-side, so the scan
is chunked (300k blocks) and decoded-and-dropped per chunk — a full window never
sits in memory. Persistence is the pipeline's **log cache**
([`log-cache.md`](log-cache.md)): the first run downloads the Diamond stream
(1.2M events on ethereum, 5.2M on base for Mar–Sep 2026 — a few GB of parquet),
later runs replay it from disk. The scan is bounded below by the program's
`start`.

## Precedence vs Li.Fi's own code 4012

Decision (ops, 2026-09-02): **the Osero tag wins in `oserofrontend` txs.** As
implemented this holds because 4012 lands on the *adapter*, not the user, so
the user's only signal in the tx is the 3900 pseudo-tag. Two things would
change that and are guarded rather than silently decided:

- a Li.Fi flow that deposits with `receiver = user` puts a **real
  `Referral(4012)` on the user** — real beats pseudo, so 4012 would win.
  `merge_referrals` now WARNs on every such override with the (tx, user,
  pseudo→real) sample; none observed in the spec txs;
- adding 4012 to `REROUTED_CODES` later would re-attach it to the user with
  re-route precedence (above pseudo-tags) — do not do that without deciding
  the `oserofrontend` case explicitly.

## Scope and known gaps

- Phase 1: **sUSDS-eth**. Wiring the same program onto sUSDC (L2s) is one
  registry entry per source once the L2 discovery scan shows volume.
- Origin chains scanned: ethereum, base, arbitrum, optimism, unichain,
  avalanche_c (the pipeline's HyperSync hosts). A bridge *from* any other chain
  (polygon, bsc, …) is invisible until a host mapping is added.
- Cross-chain deliveries whose destination call **fails** on Li.Fi's side are
  refunded in the bridged asset (no sUSDS) — nothing to tag, correct by
  construction.
- Same-chain generic swaps whose `receiver` is a contract wallet: tagged like
  any other recipient (net-positive rule); nothing Li.Fi-specific.

## Impact (measured)

Discovery, every Li.Fi Diamond event on ethereum / base / arbitrum / optimism /
unichain / avalanche_c, 2026-03-01 → 2026-09-02 (7.4M events decoded):
**3 `oserofrontend` events in total** — 1 same-chain sUSDS swap on ethereum
(2026-07-30, 0.90 sUSDS) and 2 base → ethereum bridges (2026-07-30,
2026-08-14; stargateV2), zero on every other chain (unichain has no Diamond at
all). They anchor **3 ethereum txs → 2 wallets**; one of the two is also a
direct Osero-frontend user (`Referral(3000)` in other txs — the tags interleave
by last-wins, no conflict).

With/without diff, affected wallets only, production revenue stack
(`hypersync-results/measurements/lifi_3900_delta.csv`):

| month | 3900 (USD) | displaced |
|---|---|---|
| 2026-07 | 0.00002 | 99 |
| 2026-08 | 0.00052 | 99, 0 |
| total | **0.0005** | net delta **$0.00** |

The stream is armed and correct; it is simply tiny today. The Osero volume that
*does* flow through Li.Fi is Jumper Earn's under real code **3006** —
see [`osero-codes.md`](osero-codes.md). No `susdc` wiring: zero same-chain
`oserofrontend` swaps on any L2.
