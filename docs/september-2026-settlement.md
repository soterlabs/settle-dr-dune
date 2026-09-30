# September 2026 Distribution Rewards settlement

## Window and execution status

The September window is `[2026-09-01, 2026-10-01)`. The deployed exclusive
cutoff is `2026-10-01`; the archive-coverage guard must observe every required
HyperSync archive at or beyond that instant before the full run may start.
This prevents a run on September 29 or 30 from being mistaken for a completed
calendar month.

Bootstrap the August closing state once (this can run before the October 1
archive cutoff), then run September after archive coverage reaches the cutoff:

```sh
.venv/bin/python py/run_dr_pipeline.py --end 2026-09-01 --fresh
.venv/bin/python py/run_dr_pipeline.py --fresh
.venv/bin/python py/build_dr_comparison.py
.venv/bin/python -m pytest py/tests -q
```

Workers write atomic zstd-Parquet checkpoints under
`hypersync-results/dr_full`. The manifest binds the checkpoint set to both the
exclusive end date and the checkpoint format. A failed run resumes completed
chunks. The complete August state under `hypersync-results/dr_state/2026-09-01`
seeds the September calculation, so high-volume workers fetch and replay only
September events. Separately, the HyperSync event cache stores immutable event
ranges as Parquet and downloads only uncovered blocks. The three small Skybase
protocol jobs retain their documented full-protocol replay fallback.

## Historical-payment audit for September true-ups

Audit source: `soterlabs/settlement-reports` at commit
`cb3db5ce974f22361cff8f2a0aef1bde26aa05d7`, covering the published Skybase
and Grove reports from January through August 2026. Both the Markdown summaries
and every cell in the corresponding workbooks were searched.

The Skybase workbooks contain no code 1997, 1998, or 1999 and no reference to
the three venue contracts. Therefore none of the following pipeline additions
appears in those published payments:

| Code | Venue | Jan–Aug calculated true-up (USDS) |
|---:|---|---:|
| 1997 | Pendle SY-sUSDS / PT-sUSDS backing | 27,740.24 |
| 1998 | Morpho Vault USDS Flagship | 34,229.17 |
| 1999 | Morpho Vault USDS Risk Capital | 758.75 |
| | **Total** | **62,728.16** |

Those three existing synthetic codes remain necessary because the attributed
custody positions do not emit a usable beneficiary referral code. No new
synthetic code is needed for this settlement.

The Grove farm is different: it emits ordinary on-chain referral codes. Its
payable historical additions are code 0, code 1 (Skybase), and code 1002; its
untagged `-999999` balance is non-payable. The published Skybase report totals
match the pipeline totals *before* the farm was added, to report rounding:

| Month | Code | Published Skybase DR | Farm addition absent from report |
|---|---:|---:|---:|
| 2026-07 | 0 | 2,388.72 | 22.62 |
| 2026-07 | 1 | 56,759.13 | 31,984.31 |
| 2026-07 | 1002 | 20,518.47 | 164.09 |
| 2026-08 | 1 | 38,293.67 | 29,698.52 |
| 2026-08 | 1002 | 13,461.98 | 96.63 |

This exact baseline match is the venue-level evidence that the Grove-farm
amounts were not included in the published Skybase settlements even though
the same referral codes were paid for other venues. The payable Grove-farm
true-up is **61,966.17 USDS** for July–August. A further **813.35 USDS** is
untagged and must not be paid. June contains only a sub-cent untagged artifact.

The final September settlement should therefore present separately:

1. September accrual from the completed `[2026-09-01, 2026-10-01)` run.
2. The 62,728.16 USDS codes 1997–1999 historical true-up.
3. The 61,966.17 USDS payable Grove-farm historical true-up, split by its
   emitted beneficiary codes rather than assigned a synthetic code.

This audit establishes absence from the published settlement reports; it is
not evidence about transfers performed outside that reporting repository.
