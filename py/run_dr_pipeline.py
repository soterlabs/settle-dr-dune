"""Run the full DR pipeline off HyperSync (no Dune) and write the rollups.

    .venv/bin/python py/run_dr_pipeline.py [--sources stusds,farms] [--end 2026-07-01]

Writes to hypersync-results/dr/:
    dr_monthly_combined.csv
    dr_rollup_by_refcode.csv
    dr_rollup_by_refcode_token.csv

This is the Dune-free replacement for `npm run combine`. See revenue/pipeline.py
for the per-source monthly config and the high-volume performance note.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")

from drhs.revenue import pipeline  # noqa: E402
from run_source import build_source_legs  # noqa: E402


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=",".join(pipeline.SOURCE_MONTHLY),
                    help="comma-separated subset of: " + ",".join(pipeline.SOURCE_MONTHLY))
    ap.add_argument("--end", type=_d, default=date(2026, 7, 1))
    ap.add_argument("--out", type=Path, default=ROOT / "hypersync-results" / "dr")
    args = ap.parse_args()

    per_source = {}
    for key in args.sources.split(","):
        key = key.strip()
        print(f"[dr] computing monthly DR for {key} ...", flush=True)
        per_source[key] = pipeline.source_monthly(key, args.end, build_source_legs)
        print(f"[dr]   {len(per_source[key])} monthly rows", flush=True)

    out = pipeline.combine(per_source)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, df in out.items():
        p = args.out / f"{name}.csv"
        df.to_csv(p, index=False)
        print(f"[dr] wrote {p} ({len(df)} rows)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
