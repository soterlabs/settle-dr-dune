"""End-to-end DR pipeline orchestrator — replaces combine-dr-results.ts.

Wires HyperSync TWA -> rates -> conversions -> deployment -> monthly DR for
every source, then merges the per-source monthly outputs into the cross-asset
rollups (the step that couldn't be done in one Dune query). NO Dune, no RPC.

Each source's monthly config mirrors its dr_rewards_monthly_*.sql:

  key           twa sources                 reclassify        conversion
  ------------  --------------------------  ----------------  ----------------
  susds_susdc   susds_eth + susdc           99 / 127          susds rate
  stusds        stusds                      none              stusds rate
  farms         usds_farms                  none              1.0 (USDS)
  psm3          susds_psm3 (4 L2 chains)    99 + code0 split  susds rate
  sp            sp_vaults                   130/131/132       sp rate + deploy

NOTE: high-volume sources (sUSDC, sp, L2 PSM3) over full history produce
millions of per-user-day TWA rows; the pure-Python TWA engine is the bottleneck
there (fine for a windowed run; a vectorized engine is the optimization for a
full-history production run).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from .. import twa
from . import conversion, deployment, monthly


def _susds_conv():
    return monthly.series_conv(conversion.susds_rates(), "rate")


def _stusds_conv():
    return monthly.series_conv(conversion.stusds_rates(), "rate")


# key -> (twa source names, reclassify, conv-builder, is_sp)
SOURCE_MONTHLY = {
    "susds_susdc": (["susds_eth", "susdc"], monthly.reclass_susds_susdc, _susds_conv, False),
    "stusds": (["stusds"], monthly.reclass_none, _stusds_conv, False),
    "farms": (["usds_farms"], monthly.reclass_none, lambda: monthly.const_conv, False),
    "psm3": (["susds_psm3"], monthly.reclass_psm3, _susds_conv, False),
    "sp": (["sp_vaults"], monthly.reclass_sp, None, True),
}


def source_monthly(key: str, end: date, build_legs) -> pd.DataFrame:
    """Compute one source's monthly DR. ``build_legs(src, end)`` fetches TWA legs
    (injected to avoid importing run_source here)."""
    srcs, reclass, conv_builder, is_sp = SOURCE_MONTHLY[key]
    fill = min(end, date(2026, 6, 30))
    legs = pd.concat([build_legs(s, end) for s in srcs], ignore_index=True)
    tw = twa.compute_twa(legs, fill_through=fill)
    if is_sp:
        dep = deployment.deployment_ratios(tw, end=fill)
        dep_map = {(r.blockchain, r.vault_symbol, r.dt): r.deployment_ratio for r in dep.itertuples()}
        return monthly.monthly_dr(tw, reclassify=reclass,
                                  conv_lookup=monthly.sp_conv(conversion.sp_vault_rates()),
                                  sp_deployment=dep_map)
    return monthly.monthly_dr(tw, reclassify=reclass, conv_lookup=conv_builder())


def combine(monthly_by_source: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Merge per-source monthly DR into the cross-asset rollups.

    Returns dr_monthly_combined, dr_rollup_by_refcode, dr_rollup_by_refcode_token
    (matching combine-dr-results.ts). Rollups pivot dr_usd by month with a total.
    """
    combined = pd.concat(
        [df.assign(source=src) for src, df in monthly_by_source.items() if not df.empty],
        ignore_index=True,
    )

    def _pivot(keys: list[str]) -> pd.DataFrame:
        g = combined.groupby(keys + ["month"])["dr_usd"].sum().reset_index()
        piv = g.pivot_table(index=keys, columns="month", values="dr_usd", fill_value=0.0)
        piv["total"] = piv.sum(axis=1)
        return piv.reset_index().sort_values("total", ascending=False)

    return {
        "dr_monthly_combined": combined.sort_values(["month", "blockchain", "token", "ref_code"]),
        "dr_rollup_by_refcode": _pivot(["ref_code"]),
        "dr_rollup_by_refcode_token": _pivot(["ref_code", "token"]),
    }
