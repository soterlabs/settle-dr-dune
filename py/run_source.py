"""Run one DR TWA source over HyperSync and write the shared-schema CSV.

Usage:
    .venv/bin/python py/run_source.py stusds [--end 2026-07-01] [--out PATH]

Env: ENVIO_API_TOKEN (HyperSync auth). Loaded from repo-root .env.
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

from drhs import twa  # noqa: E402
from drhs.sources import template_ab  # noqa: E402

SOURCES = {
    # Validated against Dune 7877544 (machine-precision parity, 2026-07).
    "stusds": [template_ab.STUSDS],
    "susds_eth": [template_ab.SUSDS_ETH],
    "susdc": template_ab.TEMPLATE_A_SUSDC,          # all 5 chains (production)
    # sUSDC per launch cohort — used for small, complete regression fixtures:
    # eth/base/arb went live 2025-03-05; optimism/unichain 2025-06-02.
    "susdc_mar": [template_ab.SUSDC_ETH, template_ab.SUSDC_BASE, template_ab.SUSDC_ARB],
    "susdc_jun": [template_ab.SUSDC_OPT, template_ab.SUSDC_UNI],
}

# Per-source address exclusions (Template A drops protocol/vault holders; see
# template_ab.TEMPLATE_A_EXCLUDED). stUSDS (Template B) has none.
SOURCE_EXCLUDED = {
    s: template_ab.TEMPLATE_A_EXCLUDED
    for s in ("susds_eth", "susdc", "susdc_mar", "susdc_jun")
}


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=sorted(SOURCES))
    ap.add_argument("--end", type=_parse_date, default=template_ab.DEFAULT_END,
                    help="scan cutoff (exclusive); default 2026-07-01")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    targets = SOURCES[args.source]
    excluded = SOURCE_EXCLUDED.get(args.source, frozenset())
    print(f"[{args.source}] fetching events via HyperSync ...", flush=True)
    legs = template_ab.build_legs(targets, end_date=args.end, excluded=excluded)
    print(f"[{args.source}] {len(legs)} balance-change legs; computing TWA ...", flush=True)
    df = twa.compute_twa(legs)
    print(f"[{args.source}] {len(df)} TWA rows "
          f"({df['user_addr'].nunique() if len(df) else 0} users)", flush=True)

    out = args.out or (ROOT / "hypersync-results" / f"twa_{args.source}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[{args.source}] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
