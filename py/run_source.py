"""Run one DR TWA source over HyperSync and write the shared-schema CSV.

Usage:
    .venv/bin/python py/run_source.py stusds [--end 2026-07-01] [--out PATH]

Env: ENVIO_API_TOKEN (HyperSync auth). Loaded from repo-root .env.

The SPECS registry is the single source of truth for every token: its template
module, targets, Dune query id, the label of its ref-code event group, and any
address exclusions. run_source / validate / capture_fixture / test_parity all
drive off it, so a new token is added in one place.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")

from drhs import twa  # noqa: E402
from drhs.sources import template_ab, template_c, template_d  # noqa: E402

_EXC = template_ab.TEMPLATE_A_EXCLUDED


@dataclass(frozen=True)
class SourceSpec:
    template: object              # module: build_legs, fetch_target_rows, legs_from_rows, _end_ts
    targets: list
    dune_query: int
    ref_kind: str = "referrals"   # label of the first fetched event group: referrals | swaps
    excluded: frozenset = field(default_factory=frozenset)


SPECS: dict[str, SourceSpec] = {
    # Template B — stUSDS (no exclusions). Full-history parity confirmed.
    "stusds": SourceSpec(template_ab, [template_ab.STUSDS], 7877544),
    # Template A — sUSDS / sUSDC (protocol-holder exclusions).
    "susds_eth": SourceSpec(template_ab, [template_ab.SUSDS_ETH], 7877542, excluded=_EXC),
    "susdc": SourceSpec(template_ab, template_ab.TEMPLATE_A_SUSDC, 7877542, excluded=_EXC),
    "susdc_mar": SourceSpec(
        template_ab, [template_ab.SUSDC_ETH, template_ab.SUSDC_BASE, template_ab.SUSDC_ARB],
        7877542, excluded=_EXC),
    "susdc_jun": SourceSpec(
        template_ab, [template_ab.SUSDC_OPT, template_ab.SUSDC_UNI], 7877542, excluded=_EXC),
    # Template C — L2 sUSDS via PSM3 Swap ref_code (balance from token Transfer).
    "susds_psm3": SourceSpec(template_c, template_c.ALL, 7877543, ref_kind="swaps", excluded=_EXC),
    "susds_psm3_base": SourceSpec(template_c, [template_c.BASE], 7877543, ref_kind="swaps", excluded=_EXC),
    "susds_psm3_arb": SourceSpec(template_c, [template_c.ARB], 7877543, ref_kind="swaps", excluded=_EXC),
    "susds_psm3_opt": SourceSpec(template_c, [template_c.OPT], 7877543, ref_kind="swaps", excluded=_EXC),
    "susds_psm3_uni": SourceSpec(template_c, [template_c.UNI], 7877543, ref_kind="swaps", excluded=_EXC),
    # Template D — USDS staking farms (Staked/Withdrawn balance; no exclusions).
    "usds_farms": SourceSpec(template_d, template_d.ALL, 7877545),
    "usds_farm_sky": SourceSpec(template_d, [template_d.SKY], 7877545),
    "usds_farm_spk": SourceSpec(template_d, [template_d.SPK], 7877545),
    "usds_farm_cle": SourceSpec(template_d, [template_d.CLE], 7877545),
    # Template E — Spark sp* vaults (== Template A code path; no exclusions).
    "sp_vaults": SourceSpec(template_ab, template_ab.TEMPLATE_E, 7877546),
    "sp_usdc_eth": SourceSpec(template_ab, [template_ab.SP_USDC_ETH], 7877546),
    "sp_usdc_avax": SourceSpec(template_ab, [template_ab.SP_USDC_AVAX], 7877546),
    "sp_usdt_eth": SourceSpec(template_ab, [template_ab.SP_USDT_ETH], 7877546),
    "sp_pyusd_eth": SourceSpec(template_ab, [template_ab.SP_PYUSD_ETH], 7877546),
    "sp_eth_eth": SourceSpec(template_ab, [template_ab.SP_ETH_ETH], 7877546),
}


def build_source_legs(name: str, end_date: date):
    s = SPECS[name]
    return s.template.build_legs(s.targets, end_date=end_date, excluded=s.excluded)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=sorted(SPECS))
    ap.add_argument("--end", type=_parse_date, default=template_ab.DEFAULT_END,
                    help="scan cutoff (exclusive); default 2026-07-01")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    print(f"[{args.source}] fetching events via HyperSync ...", flush=True)
    legs = build_source_legs(args.source, args.end)
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
