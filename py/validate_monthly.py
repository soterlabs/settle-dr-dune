"""Validate monthly DR (revenue/monthly.py) vs the Dune dr_rewards_monthly_* queries."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")
from drhs import twa  # noqa: E402
from drhs.revenue import conversion, monthly  # noqa: E402
from run_source import build_source_legs  # noqa: E402

H = {"X-DUNE-API-KEY": os.environ["DUNE_API_KEY"], "Content-Type": "application/json"}
B = "https://api.dune.com/api/v1"

# source -> (dune_query, twa_source(s), reclassify, conv_builder)
CONFIG = {
    "stusds": dict(dune=7877553, srcs=["stusds"], reclass=monthly.reclass_none,
                   conv=lambda: monthly.series_conv(conversion.stusds_rates(), "rate")),
    "usds_farms": dict(dune=7877554, srcs=["usds_farms"], reclass=monthly.reclass_none,
                       conv=lambda: monthly.const_conv),
    "susds_susdc": dict(dune=7877552, srcs=["susds_eth", "susdc"], reclass=monthly.reclass_susds_susdc,
                        conv=lambda: monthly.series_conv(conversion.susds_rates(), "rate")),
    # split runs to keep the flagship-token compute bounded (filter Dune to `token`)
    "susds_only": dict(dune=7877552, srcs=["susds_eth"], token="sUSDS", reclass=monthly.reclass_susds_susdc,
                       conv=lambda: monthly.series_conv(conversion.susds_rates(), "rate")),
    "susdc_only": dict(dune=7877552, srcs=["susdc"], token="sUSDC", reclass=monthly.reclass_susds_susdc,
                       conv=lambda: monthly.series_conv(conversion.susds_rates(), "rate")),
    "psm3_arb": dict(dune=7877565, srcs=["susds_psm3_arb"], reclass=monthly.reclass_psm3,
                     conv=lambda: monthly.series_conv(conversion.susds_rates(), "rate")),
    "sp": dict(dune=7877555, srcs=["sp_vaults"], reclass=monthly.reclass_sp, sp=True),
}


def dune(qid: int, end: str) -> pd.DataFrame:
    # The monthly queries are unparameterized (full history by default); filter
    # by month client-side.
    j = requests.post(f"{B}/query/{qid}/execute", json={}, headers=H, timeout=60).json()
    eid = j["execution_id"]
    while True:
        s = requests.get(f"{B}/execution/{eid}/status", headers=H, timeout=60).json()
        if s["state"] == "QUERY_STATE_COMPLETED":
            break
        if s["state"] in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise RuntimeError(s)
        time.sleep(3)
    rows, off = [], 0
    while True:
        r = requests.get(f"{B}/execution/{eid}/results", params={"limit": 5000, "offset": off},
                         headers=H, timeout=60).json()["result"]["rows"]
        rows += r; off += len(r)
        if len(r) < 5000:
            break
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=sorted(CONFIG))
    ap.add_argument("--end", default="2026-07-01")
    args = ap.parse_args()
    cfg = CONFIG[args.source]
    end_d = date.fromisoformat(args.end)
    fill = min(end_d, date(2026, 6, 30))

    print(f"[hs] computing TWA for {cfg['srcs']} ...", flush=True)
    # Dune-parity comparison: Dune monthly queries carry no synthetic programs.
    frames = [build_source_legs(s, end_d, include_synthetic=False) for s in cfg["srcs"]]
    legs = pd.concat(frames, ignore_index=True)
    tw = twa.compute_twa(legs, fill_through=fill)
    if cfg.get("sp"):
        from drhs.revenue import deployment
        dep = deployment.deployment_ratios(tw, end=fill)
        dep_map = {(r.blockchain, r.vault_symbol, r.dt): r.deployment_ratio for r in dep.itertuples()}
        conv_lookup = monthly.sp_conv(conversion.sp_vault_rates())
        m = monthly.monthly_dr(tw, reclassify=cfg["reclass"], conv_lookup=conv_lookup, sp_deployment=dep_map)
    else:
        m = monthly.monthly_dr(tw, reclassify=cfg["reclass"], conv_lookup=cfg["conv"]())
    m = m[m["month"] < args.end]
    print(f"[hs] monthly rows: {len(m)}  Σ dr_usd={m['dr_usd'].sum():.6f}", flush=True)

    dn = dune(cfg["dune"], args.end)
    dn["month"] = dn["month"].astype(str).str[:10]
    dn = dn[dn["month"] < args.end]
    if cfg.get("token"):                       # split run: compare one token only
        dn = dn[dn["token"] == cfg["token"]]
        m = m[m["token"] == cfg["token"]]
    keys = ["month", "blockchain", "token", "ref_code"]
    dn["ref_code"] = dn["ref_code"].astype(float).astype(int)
    m["ref_code"] = m["ref_code"].astype(int)
    mm = m.merge(dn[keys + ["dr_usd"]], on=keys, how="outer", suffixes=("_hs", "_dn"), indicator=True)
    both = mm[mm["_merge"] == "both"].copy()
    both["absdiff"] = (both["dr_usd_hs"] - both["dr_usd_dn"].astype(float)).abs()
    print(f"\n==== {args.source} MONTHLY DR vs Dune {cfg['dune']} ====")
    print(f"rows HS={len(m)} Dune={len(dn)} matched={len(both)} "
          f"only_hs={int((mm._merge=='left_only').sum())} only_dune={int((mm._merge=='right_only').sum())}")
    print(f"Σ dr_usd HS={m['dr_usd'].sum():.6f} Dune={dn['dr_usd'].astype(float).sum():.6f} "
          f"reldiff={abs(m['dr_usd'].sum()-dn['dr_usd'].astype(float).sum())/max(abs(dn['dr_usd'].astype(float).sum()),1e-9):.3e}")
    print(f"max per-cell absdiff={both['absdiff'].max():.3e}")
    unm = mm[mm["_merge"] != "both"]
    if len(unm):
        print("unmatched cells (sample):")
        print(unm.head(10).to_string(index=False))
    worst = both.sort_values("absdiff", ascending=False).head(6)
    print(worst[keys + ["dr_usd_hs", "dr_usd_dn", "absdiff"]].to_string(index=False))


if __name__ == "__main__":
    main()
