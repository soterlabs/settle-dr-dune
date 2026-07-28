# Skybase 2026 DR: calculated vs paid

**Date:** 2026-07-28. **Scope:** Skybase prime agent only, calendar 2026
(settled window Jan–Jun). **Data:** [`skybase_2026_dr_reconciliation.csv`](skybase_2026_dr_reconciliation.csv)
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

## Headline reconciliation (Feb–Jun 2026, USD)

| code | partner | calc Jan | calc Feb–Jun | paid Feb–Jun | diff (paid−calc) |
|---|---|---|---|---|---|
| 0 | Skybase (code 0) | 1,281 | 36,500 | 38,592 | **+2,092** |
| 1 | Skybase (code 1) | 128,866 | 566,730 | 580,710 | **+13,980** |
| 1001 | summerfi | 2,251 | 6,393 | 6,413 | +20 |
| 1002 | defisaver | 1,970 | 132,881 | 132,890 | +9 |
| 1003 | cow | 72,534 | 229,065 | 0 | **−229,065** |
| 1004 | paraswap | 3,343 | 8,515 | 0 | **−8,515** |
| 1007 | yearn | 10,083 | 54,479 | 54,478 | −1 |
| 1015 | MOM | 0 | 0 | 0 | 0 |
| 1016 | lazysummer | 1,201 | 177 | 178 | +1 |
| 1017 | (1017) | 536 | 951 | 952 | +1 |
| 10000 | L2 PSM3 default code 0 | 374 | 5,054 | *(memo — not a sheet column)* | |

Sheet DR subtotal Feb–Jun: **$814,214 paid** vs **$1,035,691 calculated**
for the same code set (difference dominated by the two unpaid aggregators).

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
6. **Rate-change flag:** the notes say the DR rate dropped 0.5% → 0.2%
   "starting for June 2026", yet the June amounts actually paid (e.g. code 1
   $117,784) are clearly still at 0.5%-scale and match our 0.5% calculation.
   Our locked rate table has no June cut. If the cut is real and
   retroactive, June is materially overpaid by both systems; if it applies
   from July ("BOOSTED DR Changed July 9th"), our `rates_dr` needs the new
   tier before any July settlement is computed. **Must be resolved before
   the next MSC.**

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
