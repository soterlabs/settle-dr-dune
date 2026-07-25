# Lazy Summer (1016): what it is and how to port it

**Date:** 2026-07-24. Investigation triggered by the DR-partner classification
(see `cowswap-1003-double-attribution.md`): Amatsu pays partner **1016
"lazysummer"** $69.4k cumulative, with an anomalous $44.8k in Apr 2026 —
and the settlement-handover repo contains **no trace of it** (searched: no
`1016`, `lazysummer`, `fleet`; their hardcoded partner map stops at
`{1001 summerfi, 1002 defisaver, 1004 paraswap, 1007 yearn}`; their tracked-
venue config has no fleet contracts).

## Findings (on-chain + Amatsu data)

1. **1016 is Summer.fi's Lazy Summer Protocol** (fleet vaults / FleetCommander
   + Arks; repo `OasisDEX/lazy-summer-protocol`). It **succeeds 1001
   summerfi**: 1001 payouts stop exactly at Jan 2026 while 1016 scales up —
   confirming the note in `compare-dr.ts`.
2. **On-chain referral footprint is almost nil**: 47 `Referral(1016)` events
   on the SKY farm (owners are Summer.fi automation wallets, explains the
   ~$1.5k SKY-farm rows), 2 on SPK farm, and **zero** on mainnet sUSDS,
   sUSDC-eth, CLE farm, and all four L2 PSM3s (1.98M swaps scanned). The
   sUSDS payouts cannot be reconstructed from referral events — Amatsu
   attributes 1016 **by contract address in their Supabase DB** (`partners`
   table), built after the handover snapshot.
3. **The balance basis is the fleets' sky Arks** (curation config: `ark:
   "sky"`, `arkSymbol: sUSDS`). Measured mainnet USDC-fleet sUSDS holdings:
   5.1M (Feb) → 3.9M (Mar) → 2.7M (Apr) → 1.7M (May); at the 0.5% XR rate
   that yields $1.5–2k/month — consistent with 1016's Nov–Mar sUSDS trend
   ($0.8–4k/mo incl. the USDT fleet). These Ark balances sit in **our
   untagged 99 bucket** today.
4. **Apr 2026's $44,758 is NOT a balance.** No fleet/Ark/candidate contract
   held anything near the implied ~$100M sUSDS that month (top-untagged-holder
   reconstruction + per-month Ark balance checks). It is almost certainly a
   one-time retroactive true-up (plausibly back-crediting fleet history
   and/or folding 1001 into 1016 at migration).

## Resolution (2026-07-25 — Amatsu unavailable for questions; resolved from data)

New evidence closing the case:

- **Identity is definitive**: 1016's payout wallet
  `0x447BF9d1485ABDc4C1778025DfdfbE8b894C3796` (from
  `distribution-rewards-payouts-…_susdsFarm.csv`) is Lazy Summer's
  **governance timelock** (`lazy-summer-protocol` `config/index.json`,
  `gov.timelock`).
- **Spark's own Dune data agrees with us, not Amatsu**: Spark reports 1016 =
  $1,378 total (USDS-SKY/SPK farm events only) — matching our numbers to the
  cent (Diff Soter-Spark = 0). The reconciliation workbook already carried
  the open note *"Amatsu included DR for sUSDC, sUSDS … unknown which
  balances or events to use."* Those balances are now identified: the fleet
  sky Arks.
- **The Apr 2026 $44,758 has NO balance basis anywhere.** Checked at
  multiple April dates: mainnet arks 3.9M→2.7M sUSDS (shrinking), base arks
  1.0M→0 (wound down), whole-fleet `totalAssets()` only $11–12M (×0.5%/12 ≈
  $500/mo), 1001's folded history doesn't match ($89k lifetime, $18.8k
  sUSDS). Even crediting every Lazy-Summer-related balance at the max
  boosted rate cannot produce $44.8k for April.
- Steady-state months (Feb $2,988 / Mar $3,971) are consistent with the
  ark balances (mainnet ~4–5M + base ~1M) at a boosted rate ≈0.5–0.7%.

**Verdict**: 1016 = fleet sky-Ark balances (address-tagged in Amatsu's DB) +
a **one-time off-chain adjustment of ≈ $43.5k in Apr 2026** that is not
reproducible from any on-chain data. Treatment in our pipeline:

1. Reconstruct the on-chain-backed component via class-D ark tagging (below).
2. Book the Apr 2026 delta as a **documented permanent diff vs Amatsu**
   (manual-adjustment line in the comparison workbook, like other known
   diffs) — NOT as event-derived DR. If the payout was real, it should be
   traceable in the payout wallet's inflows, not in DR methodology.

## Port plan (class D — contract-tagged source, like Aave 9001)

- **Address set**: the fleet deployment JSONs in
  `OasisDEX/lazy-summer-protocol/packages/deployment/deployments/fleets/`
  are authoritative and versioned — mainnet USDC fleet
  `0x98C49e13bf99D7CAd8069faa2A370933EC9EcF17` (15 arks + buffer; the sky
  ark observed holding sUSDS is `0x9890C99f504337C3500AC05c267c38dfcd41C3e2`),
  plus the USDT fleet and Base/Arbitrum fleets for L2 coverage.
- **Mechanism**: attribute the Arks' sUSDS balances to 1016 — either a
  dedicated per-contract source (the 9001 pattern) or, cleaner, wallet-level
  reattribution inside the existing susds source (tag the Ark addresses'
  balances 1016 instead of 99; single-stream, no double counting).
- ~~Ask Amatsu~~ — no longer possible; resolved from data (see Resolution
  above). Residual unknowns accepted: the exact boost rate applied to 1016
  (implied ≈0.5–0.7%) and the composition of the Apr 2026 adjustment.
- **Not an aggregator**: do NOT add 1016 to `REROUTED_CODES` or as a
  `SyntheticProgram` — the Arks retain their balances (vault shape); the
  47 farm events flow through the normal referral path already.
