"""Skybase 2026 DR reconciliation: calculated (HyperSync pipeline) vs paid.

Reads hypersync-results/dr/dr_monthly_combined.csv (produced by
run_dr_pipeline.py) and compares, per ref code and month of 2026, against the
DR amounts Skybase was actually paid per the published reconciliation sheet
("Copy of Demand-Side Cleanup MSC #11", Skybase Reconciliation tab, fetched
2026-07-28). Writes docs/skybase_2026_dr_reconciliation.csv and prints the
markdown tables embedded in docs/skybase-2026-dr-reconciliation.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
MONTHS = [f"2026-{m:02d}" for m in range(1, 7)]

PARTNER = {0: "Skybase (code 0)", 1: "Skybase (code 1)", 1001: "summerfi",
           1002: "defisaver", 1003: "cow", 1004: "paraswap", 1007: "yearn",
           1015: "MOM", 1016: "lazysummer", 1017: "(1017)",
           10000: "L2 PSM3 default code 0 (memo)"}

# Paid DR per MSC month, transcribed from the sheet's per-code block
# (columns: Skybase 0/1, summerfi..1017; MSC 6..10 = Feb..Jun 2026).
# Jan 2026 has no DR payment line anywhere in the sheet (the Mar-30
# reconciliation payment carried DR = 0), hence None.
PAID: dict[int, dict[str, float | None]] = {
    0:    {"2026-01": None, "2026-02": 1121, "2026-03": 3663, "2026-04": 11018, "2026-05": 15292, "2026-06": 7498},
    1:    {"2026-01": None, "2026-02": 96894, "2026-03": 120812, "2026-04": 116183, "2026-05": 129037, "2026-06": 117784},
    1001: {"2026-01": None, "2026-02": 1601, "2026-03": 1655, "2026-04": 1434, "2026-05": 913, "2026-06": 810},
    1002: {"2026-01": None, "2026-02": 2971, "2026-03": 29196, "2026-04": 28955, "2026-05": 33175, "2026-06": 38593},
    1003: {"2026-01": None, "2026-02": 0, "2026-03": 0, "2026-04": 0, "2026-05": 0, "2026-06": 0},
    1004: {"2026-01": None, "2026-02": 0, "2026-03": 0, "2026-04": 0, "2026-05": 0, "2026-06": 0},
    1007: {"2026-01": None, "2026-02": 13745, "2026-03": 15859, "2026-04": 14112, "2026-05": 10762, "2026-06": 0},
    1015: {"2026-01": None, "2026-02": 0, "2026-03": 0, "2026-04": 0, "2026-05": 0, "2026-06": 0},
    1016: {"2026-01": None, "2026-02": 168, "2026-03": 9, "2026-04": 1, "2026-05": 0, "2026-06": 0},
    1017: {"2026-01": None, "2026-02": 350, "2026-03": 370, "2026-04": 215, "2026-05": 9, "2026-06": 8},
}


def main() -> int:
    df = pd.read_csv(REPO / "hypersync-results" / "dr" / "dr_monthly_combined.csv")
    df["m"] = df["month"].str[:7]
    codes = list(PAID) + [10000]
    sub = df[(df["m"].isin(MONTHS)) & df["ref_code"].isin(codes)]
    calc = sub.groupby(["ref_code", "m"])["dr_usd"].sum()

    rows = []
    for code in codes:
        for m in MONTHS:
            c = round(float(calc.get((code, m), 0.0)), 2)
            p = PAID.get(code, {}).get(m)
            rows.append({
                "month": m, "ref_code": code, "partner": PARTNER[code],
                "calculated_dr_usd": c,
                "paid_dr_usd": "" if p is None else p,
                "diff_paid_minus_calc": "" if p is None else round(p - c, 2),
            })
    out = pd.DataFrame(rows)
    csv = REPO / "docs" / "skybase_2026_dr_reconciliation.csv"
    out.to_csv(csv, index=False)
    print(f"wrote {csv} ({len(out)} rows)\n")

    # markdown table: per code, Feb-Jun cumulative calc vs paid
    print("| code | partner | calc Jan | calc Feb–Jun | paid Feb–Jun | diff (paid−calc) |")
    print("|---|---|---|---|---|---|")
    for code in codes:
        jan = float(calc.get((code, "2026-01"), 0.0))
        c = sum(float(calc.get((code, m), 0.0)) for m in MONTHS[1:])
        pvals = [PAID.get(code, {}).get(m) for m in MONTHS[1:]]
        if all(v is None for v in pvals):
            print(f"| {code} | {PARTNER[code]} | {jan:,.0f} | {c:,.0f} | (memo) | |")
            continue
        p = sum(v or 0 for v in pvals)
        print(f"| {code} | {PARTNER[code]} | {jan:,.0f} | {c:,.0f} | {p:,.0f} | {p - c:+,.0f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
