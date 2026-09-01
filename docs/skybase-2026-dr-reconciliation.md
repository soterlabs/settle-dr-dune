# Skybase 2026 DR: calculated vs paid

> **Update 2026-07-29:** the payment side (incl. every tx hash) has been
> verified on-chain and the open items below were resolved or sharpened —
> see [`skybase-2026-payment-verification.md`](skybase-2026-payment-verification.md).
> Headlines: netting model verified (Skybase forwarded $0 to partners since
> Feb, so no partner double-pays); Jan 2026 assumed covered by the CC-buffer
> payment; June accrues at 0.5% (0.2% starts July); ONE open item — the
> yearn payment address.

**Date:** 2026-07-28 (July calc added 2026-08-04; August calc added
2026-09-01). **Scope:** Skybase prime agent only, calendar 2026 (settled
window Jan–Aug). **Data:**
[`skybase_2026_dr_reconciliation.csv`](skybase_2026_dr_reconciliation.csv)
(regenerate with `py/build_skybase_reconciliation.py`).

## Sources

- **Calculated** — `hypersync-results/dr/dr_monthly_combined.csv`, the merged
  chunked pipeline (`run_dr_pipeline.py`, main @ `bb653f7`): 100%
  on-chain-derived (HyperSync event logs; locked protocol rates; event-derived
  conversions), aggregator codes in the unified attribution stream, class-D
  holders on intraday TWA. Validated: exact equivalence run + workbook Checks
  all green (see `prd-chunked-pipeline.md`).
- **Paid** — the published reconciliation sheet *"Copy of Demand-Side Cleanup
  MSC #11"*, **Skybase Reconciliation** tab (fetched 2026-07-28), which
  carries payment tx hashes (e.g. MSC 6 Feb 2026: `0xa02c0c68…e98cd5db`,
  225,299 total incl. 116,850 DR) and a per-ref-code DR breakdown for
  MSC 6–10 = **Feb–Jun 2026**. Skybase's DR covers its own codes 0/1 plus
  the legacy partners it forwards for: 1001, 1002, 1003, 1004, 1007, 1015,
  1016, 1017.

## Headline reconciliation (Feb–Jun 2026 paid window + Jul/Aug calc, USD)

| code | partner | calc Jan | calc Feb–Jun | paid Feb–Jun | diff (paid−calc) | calc Jul+Aug (unpaid) |
|---|---|---|---|---|---|---|
| 0 | Skybase (code 0) | 1,281 | 36,500 | 38,592 | **+2,092** | 3,994 |
| 1 | Skybase (code 1) | 128,866 | 566,730 | 580,710 | **+13,980** | 95,053 |
| 1001 | summerfi | 2,251 | 6,393 | 6,413 | +20 | 718 |
| 1002 | defisaver | 1,970 | 132,881 | 132,890 | +9 | 33,980 |
| 1003 | cow | 72,534 | 229,065 | 0 | **−229,065** | 24,116 |
| 1004 | paraswap | 3,343 | 8,515 | 0 | **−8,515** | 983 |
| 1007 | yearn | 10,083 | 54,479 | 54,478 | −1 | 0 |
| 1015 | MOM | 0 | 0 | 0 | 0 | 0 |
| 1016 | lazysummer | 1,201 | 177 | 178 | +1 | 0 |
| 1017 | (1017) | 536 | 951 | 952 | +1 | 8 |
| 10000 | L2 PSM3 default code 0 | 374 | 5,054 | *(memo — not a sheet column)* | | 599 |

Sheet DR subtotal Feb–Jun: **$814,214 paid** vs **$1,035,691 calculated**
for the same code set (difference dominated by the two unpaid aggregators).

**July 2026** (settled 2026-08, no payment lines yet): calculated at the
blended rate — XR 0.5% through 2026-07-08, **0.2% from 2026-07-09** (Boosted-DR
termination, Atlas Edit Weekly Cycle week of 2026-07-06; matches the sheet's
"BOOSTED DR Changed July 9th" note). Skybase code-set July total: **$95,303**
(of which code 1 $56,759, cow $14,516; L2 code-0 memo adds $420).

**August 2026** (settled 2026-09-01, no payment lines yet): fully at 0.2% XR.
Skybase code-set August total: **$63,548** (of which code 1 $38,294,
defisaver $13,462, cow $9,600, code 0 $1,605; L2 code-0 memo adds $179).
Jul+Aug unpaid accrual for the code set: **$158,852**.

## Findings

1. **The pass-through codes cross-validate to the dollar.** 1001/1002/1007/
   1016/1017 differ by −1…+20 USD over five months. Two independent systems —
   our from-genesis on-chain reconstruction and the MSC payment sheet —
   agree to rounding. This is the strongest external validation the pipeline
   has had.
2. **Skybase's own codes were paid slightly ABOVE calculation**: code 1
   +$13,980 (+2.5%), code 0 +$2,092 (+5.7%) over Feb–Jun. The sheet itself
   suspects the cause (*"OUTSTANDING: Skybase received some money for PSM3
   they shouldn't have for code 0 & maybe 1"*): on L2s, default PSM3
   attribution splits to synthetic 10000/10001 in our methodology, and the
   L2 code-0 memo row ($5,054 Feb–Jun) is in the right range to explain the
   code-0 gap and part of code 1's. Recommended treatment: reconcile the
   +$16k as PSM3-sourced overpayment, per the sheet's own note.
3. **cow (1003): $229k calculated, $0 paid via Skybase (Feb–Jun).** The notes
   tab says cow was last paid *directly* (out of the CC buffer) for February.
   So Mar–Jun 2026 cow DR ($165,789 calculated) appears genuinely unpaid.
   Important context from our audit (`cowswap-1003-double-attribution.md`):
   these figures are the **clean single-stream numbers** — the sheet's remark
   *"From Lako: Sep 2024–Jun 2026 CoW swap was overpaid 691k"* refers to the
   double-count our methodology eliminates, so any settlement negotiation
   should start from this table, not from legacy sticky-tag numbers.
4. **paraswap (1004): $8,515 calculated, never paid** — the notes say
   Paraswap "didn't seem to want the money". Tracked, available if claimed.
5. **January 2026 has no DR payment anywhere in the sheet** (the Mar-30
   reconciliation payment carried DR = 0). Calculated January DR for the
   Skybase code set: **$222,065** (of which code 1 $128,866, cow $72,534).
   Either January was settled outside this sheet or it is outstanding —
   needs a definitive answer from the MSC side.
6. **Rate-change flag — RESOLVED 2026-08-04.** The cut is the termination of
   the +0.3% **Boosted Distribution Reward Rate** on top of the 0.2% base
   (Atlas Edit Weekly Cycle, week of 2026-07-06 — forum thread 28028),
   ratified **2026-07-09** — matching the sheet's "BOOSTED DR Changed July
   9th". June accrues fully at 0.5% (confirmed; the paid June amounts match
   our 0.5% calculation). `rates_dr` now carries the tier: XR 0.5% through
   2026-07-08, 0.2% from 2026-07-09 (`py/drhs/revenue/rates.py`; boundary
   locked in `py/tests/test_revenue.py`). XR* (already 0.2% base, never
   boosted) and XR-stUSDS (0.1%) are unaffected. July DR above is computed
   on this blended schedule.

## Method notes

- Calculated values are attribution-complete: aggregator programs (1003/1004)
  are net of the cross-program overlap corrections; yearn is its real on-chain
  referral events (vault-retaining, never re-routed); code 0 is Ethereum-only
  explicit referral zero (L2 zero is 10000/10001 by construction).
- "Paid" is transcribed verbatim from the sheet's per-code block; the sheet's
  own monthly totals (116,851 / 171,564 / 171,918 / 189,188 / 164,693) foot
  to its $814,214 subtotal.
- 4011 (1inch) is tracked by our pipeline ($76 all-time) but is not a Skybase
  column and is excluded here.
