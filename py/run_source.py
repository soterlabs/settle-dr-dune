"""Run one DR TWA source over HyperSync and write the shared-schema CSV.

Usage:
    .venv/bin/python py/run_source.py stusds [--end 2026-08-01] [--out PATH]

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
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")

from drhs import twa  # noqa: E402
from drhs.sources import custody, holder, template_ab, template_c, template_d  # noqa: E402

_EXC = template_ab.TEMPLATE_A_EXCLUDED


@dataclass(frozen=True)
class SourceSpec:
    template: object              # module: build_legs, fetch_target_rows, legs_from_rows, _end_ts
    targets: list
    dune_query: int
    ref_kind: str = "referrals"   # label of the first fetched event group: referrals | swaps
    excluded: frozenset = field(default_factory=frozenset)
    # synthetic aggregator programs (template A only): pseudo-referral tagging
    # of aggregator deliveries — see template_ab.SyntheticProgram.
    synthetic: tuple = ()
    # referral codes re-routed from their emitting intermediary to the end
    # recipient (template A only) — see template_ab.REROUTED_CODES.
    reroute: frozenset = field(default_factory=frozenset)
    # custody perimeters: named Morpho positions counted as still-held
    # (template A only) — see drhs.sources.custody.
    custody: tuple = ()


SPECS: dict[str, SourceSpec] = {
    # Template B — stUSDS (no exclusions). Full-history parity confirmed.
    "stusds": SourceSpec(template_ab, [template_ab.STUSDS], 7877544),
    # Template A — sUSDS / sUSDC (protocol-holder exclusions). sUSDS eth also
    # carries the aggregator programs: CowSwap 1003 (delivery pseudo-referrals)
    # and the re-routed router codes 1004 Paraswap / 4011 1inch — see
    # docs/cowswap-1003-double-attribution.md + docs/adding-an-aggregator.md.
    "susds_eth": SourceSpec(template_ab, [template_ab.SUSDS_ETH], 7877542, excluded=_EXC,
                            synthetic=(template_ab.COWSWAP,),
                            reroute=template_ab.REROUTED_CODES,
                            custody=(custody.OSERO_GTSKYLOOPING,)),
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
    # Template F — class-D contract-tagged holders (full contract balance to a
    # synthetic code; intraday TWA — see drhs/sources/holder.py).
    "usds_aave": SourceSpec(holder, [holder.AAVE_USDS], 7877569),
    "usds_ref4001": SourceSpec(holder, [holder.BRIDGE_USDS], 7877570),
    # Template E — Spark sp* vaults (== Template A code path; no exclusions).
    "sp_vaults": SourceSpec(template_ab, template_ab.TEMPLATE_E, 7877546),
    "sp_usdc_eth": SourceSpec(template_ab, [template_ab.SP_USDC_ETH], 7877546),
    "sp_usdc_avax": SourceSpec(template_ab, [template_ab.SP_USDC_AVAX], 7877546),
    "sp_usdt_eth": SourceSpec(template_ab, [template_ab.SP_USDT_ETH], 7877546),
    "sp_pyusd_eth": SourceSpec(template_ab, [template_ab.SP_PYUSD_ETH], 7877546),
    "sp_eth_eth": SourceSpec(template_ab, [template_ab.SP_ETH_ETH], 7877546),
}


def build_source_legs(name: str, end_date: date, *, include_synthetic: bool = True,
                      targets: list | None = None):
    """``include_synthetic=False`` builds the pre-synthetic (Dune-parity) legs —
    used by validate.py, since the Dune queries carry no synthetic programs
    (this switch also disables re-routed codes). ``targets`` restricts the
    source to a subset of its targets (the chunked pipeline runs one target
    per subprocess) — all other SourceSpec wiring stays identical."""
    s = SPECS[name]
    kw = {}
    if include_synthetic:
        if s.synthetic:
            kw["synthetic"] = s.synthetic
        if s.reroute:
            kw["reroute"] = s.reroute
        if s.custody:
            kw["custody"] = s.custody
    return s.template.build_legs(targets if targets is not None else s.targets,
                                 end_date=end_date, excluded=s.excluded, **kw)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=sorted(SPECS))
    ap.add_argument("--end", type=_parse_date, default=template_ab.DEFAULT_END,
                    help=f"scan cutoff (exclusive); default {template_ab.DEFAULT_END} (the settled window)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    print(f"[{args.source}] fetching events via HyperSync ...", flush=True)
    legs = build_source_legs(args.source, args.end)
    print(f"[{args.source}] {len(legs)} balance-change legs; computing TWA ...", flush=True)
    # end is exclusive, the fill day inclusive — same expression as the chunk
    # worker, so a windowed run's fill tail stops with its scan window.
    df = twa.compute_twa(
        legs, fill_through=min(args.end, template_ab.DEFAULT_END) - timedelta(days=1))
    print(f"[{args.source}] {len(df)} TWA rows "
          f"({df['user_addr'].nunique() if len(df) else 0} users)", flush=True)

    out = args.out or (ROOT / "hypersync-results" / f"twa_{args.source}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[{args.source}] wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
