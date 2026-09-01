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
from datetime import date
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from drhs.window import LAST_SETTLED_DAY  # noqa: E402

# Settled months of 2026 — DERIVED from the deployed window (drhs/window.py)
# so a settlement bump flows here without a second hand-edit; the guard in
# main() only catches data-too-short, never a stale month list.
MONTHS = [f"2026-{m:02d}" for m in
          range(1, (LAST_SETTLED_DAY.month if LAST_SETTLED_DAY.year == 2026 else 12) + 1)]

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


# The sheet's payment window, DERIVED from the transcribed payments so that
# adding a new snapshot's payment lines to PAID automatically flows into the
# summary table. Months in MONTHS outside it carry calculated DR only.
PAID_MONTHS = sorted({m for d in PAID.values() for m, v in d.items() if v is not None})


def _mon(m: str) -> str:
    return date(int(m[:4]), int(m[5:7]), 1).strftime("%b")


def main() -> int:
    df = pd.read_csv(REPO / "hypersync-results" / "dr" / "dr_monthly_combined.csv")
    df["m"] = df["month"].str[:7]
    have_max = df["m"].max()
    if have_max < MONTHS[-1]:
        raise SystemExit(
            f"dr_monthly_combined.csv reaches only {have_max} but the reconciliation "
            f"spans through {MONTHS[-1]} — re-run run_dr_pipeline.py for the extended "
            "window first (a missing month would silently print as $0 calculated)")
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

    # markdown table: per code, cumulative calc vs paid over the sheet's
    # payment window; Jan and the post-window months (calculated, no payment
    # line yet) as their own columns.
    span = f"{_mon(PAID_MONTHS[0])}–{_mon(PAID_MONTHS[-1])}"
    unpaid_months = [m for m in MONTHS if m > PAID_MONTHS[-1]]
    unpaid_label = "/".join(_mon(m) for m in unpaid_months) or "-"
    print(f"| code | partner | calc Jan | calc {span} | paid {span} | diff (paid−calc) | calc {unpaid_label} (unpaid) |")
    print("|---|---|---|---|---|---|---|")
    for code in codes:
        jan = float(calc.get((code, "2026-01"), 0.0))
        unpaid = sum(float(calc.get((code, m), 0.0)) for m in unpaid_months)
        c = sum(float(calc.get((code, m), 0.0)) for m in PAID_MONTHS)
        pvals = [PAID.get(code, {}).get(m) for m in PAID_MONTHS]
        if all(v is None for v in pvals):
            print(f"| {code} | {PARTNER[code]} | {jan:,.0f} | {c:,.0f} | (memo) | | {unpaid:,.0f} |")
            continue
        p = sum(v or 0 for v in pvals)
        print(f"| {code} | {PARTNER[code]} | {jan:,.0f} | {c:,.0f} | {p:,.0f} | {p - c:+,.0f} | {unpaid:,.0f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
