# Skybase 2026 payments: on-chain verification of Reconciliation v2

**Date:** 2026-07-29. Review of the "Reconciliation v2 – Feb 26 – June 26"
sheet (published Google Sheet, gid 1830121963), which reconciles payments made
or owed to Skybase against our pipeline's accruals
(`hypersync-results/dr/dr_monthly_combined.csv` — the sheet consumes our
per-code numbers verbatim ✓). Companion to
[`skybase-2026-dr-reconciliation.md`](skybase-2026-dr-reconciliation.md).
Audit scripts: `py/verify_skybase_payments.py`,
`py/scan_l2_zero_code_receivers.py`.

## 1. All payment txs verified on-chain

| Payment | Tx | Verified |
|---|---|---|
| CC buffer, Sep 2024–Jan 2026, $1,680,846.40 → Skybase msig `0x58b945c8…a621` | `0xfdcf740aa83b602b6c67184e9a220787d3104784faa5134ab42f45428353a1ba` (2026-03-04) | ✓ |
| MSC 6–10 (Feb–Jun): 203,134 / 225,299 / 201,469 / 1,806,616 / 204,242 → `0x08978e37…1140` | `0xbebdd875…`, `0xa02c0c68…`, `0xb03f728b…`, `0xa2bffc99…`, `0x6edea958…` | ✓ all five, exact amounts |
| Direct Feb partner payments (same 3/4 tx): cow 52,633.81 / yearn 13,746.65 / defisaver 2,968.29 / lazysummer 3,365.37 | inside `0xfdcf740a…` | ✓ (yearn wallet caveat, §3) |
| DefiSaver true-up 58,138 (Mar+Apr) | `0x6b9dcb6e04ddc3c5b98fad38fa291297f7d1be78d09a77491ce9106a61cf90e8` (2026-06-30) | ✓ |

The sheet's totals foot: DR paid via MSC $814,214; paid direct $130,852;
MSC AR+GR portion ($1,826,546) matches accrued AR+GR to $3.

## 2. The netting model ("Total Owed to Skybase = 90,626") is VERIFIED

The model treats direct partner payments as advances against Skybase's
pass-through account. Its critical assumption — that Skybase did not *also*
forward those partners' shares — is confirmed on-chain: **USDS outflows from
both Skybase addresses since 2026-02-01 include zero transfers to any partner
wallet** (7 outflows, all internal/treasury moves). Consequently no partner
was double-paid, and the netting arithmetic is sound.

Skybase's onward obligations to partners once the 90,626 lands (accrued minus
direct payments received):

| Partner | Accrued Feb–Jun | Received direct | Skybase owes onward |
|---|---|---|---|
| cow (1003) | 229,065.50 | 52,633.81 | 176,431.69 |
| defisaver (1002) | 132,880.64 | 61,106.29 | 71,774.35 |
| yearn (1007) | 54,479.50 | 13,746.65 | 40,732.85 (blocked on §3) |
| summerfi (1001) | 6,392.72 | 0 | 6,392.72 |
| (1017) | 951.37 | 0 | 951.37 |
| lazysummer (1016) | 177.11 | 3,365.37 | 0 (overpaid — §4) |
| **Total** | | | **296,282.98** |

Chain-confirmed corollary: cow's one and only payment ever is the Feb direct
(3/4). Nothing since, from anyone.

## 3. OPEN: the yearn payment address

The Feb payment (13,746.65) went to `0x5a74cb32d36f2f517db6f7b0a0591e09b22cde69`
— **not** yearn's wallet of record in the Amatsu payout ledgers
(`0xFEB4acf3df3cDEA7399794D0869ef76A6EfAff52`, yearn's long-standing treasury
multisig; the same ledger column that matched cow/defisaver/lazysummer
payments exactly). Facts established:

- `0x5a74…` is a **forwarder contract**: every USDS it ever received (3
  inflows since Aug 2025) is swept in full to `0x590dd9399bb53f1085097399c3265c7137c1c4cf`
  (another contract, 4.2KB).
- The payer sent a **1 USDS test on 2026-02-14**, three weeks before the real
  payment — the address was deliberately supplied to whoever executed it.

**Action:** hold yearn's onward $40,732.85 until (a) whoever executed the 3/4
payment states where `0x5a74…` came from, and (b) yearn confirms in writing
that `0x5a74…` / `0x590d…` are theirs. If disowned, the 13,746.65 joins §4
and yearn's entitlement reverts to the full 54,479.50.

## 4. Decision line: lazysummer overpaid $3,188.26

Direct Feb payment 3,365.37 (legacy Amatsu-computed) vs total Feb–Jun accrual
177.11 (clean methodology). The netting currently makes Skybase absorb it.
Book explicitly as recover-from-Summer.fi or Sky-side write-off.

## 5. Composition of the non-payable L2 buckets (full four-chain scan)

Receiver-level scan of every zero-code sUSDS-out PSM3 swap since Sep 2024:

- **10001 is the sUSDC vaults' backing — and nothing else — on all four
  chains.** Every tagged swap's receiver is that chain's sUSDC vault
  (Arbitrum 16,106 swaps; Optimism 611M sUSDS cumulative; Unichain 1.54B;
  Base 187M). The other listed addresses (ALM proxies, PSM3s, Compound USDS,
  Parallel) contribute **zero** — they never acquire sUSDS via tagged swaps.
  Their large balances (e.g. Arbitrum ALM ~131M, PSM3 ~16M sUSDS) sit in
  bucket **99**, protected from payment by convention only.
- The value inside 10001 already earns its real DR in the `susdc` venue
  (paid to sUSDC holders at XR\*); 10001's dr_usd is notional and must never
  join a payable total.
- **10000** (end users who defaulted the referral field) is flow, not stock:
  Base shows 208,850 swaps by 3,902 wallets moving 246M sUSDS for $2,988 of
  all-time DR; all chains together: **$7.2k all-time**.

**Follow-ups — resolved 2026-07-29:** the `NON_PAYABLE_CODES` registry
{-999999, 99, 127, 130, 131, 132, 10000, 10001} is implemented
(`drhs.revenue.monthly`) and enforced in the workbook's Payable view: those
codes appear only in a labeled "SYNTHETIC & UNPAID" section, never among
payable amounts. The address-based infra split (moving ALM/PSM3 out of 99
into 10001) was considered and DESCOPED by ops decision — both buckets are
non-payable, so the split changes no payment; the composition facts above
document where the balances sit.

## 6. Resolved review items (from the v2 sheet discussion)

- Skybase addresses acknowledged by ops: `0x58b945c8…` = Skybase-controlled
  msig (CC-buffer recipient); `0x08978e37…` = MSC recipient.
- Scope is Feb–Jun 2026; **Jan 2026 is assumed covered by the CC-buffer
  payment** (our calculated Jan DR for the code set: $222,065 — assumption
  carries that much weight).
- June 2026 accrues at 0.5% for all Skybase partners (confirmed; the 0.2%
  change lands in July → `rates_dr` needs the new tier before MSC 11).
- Payments through Feb were computed on legacy Amatsu numbers; accruals (and
  everything forward) are on our clean methodology — residuals in the diff
  column are interpretable with that in mind.
