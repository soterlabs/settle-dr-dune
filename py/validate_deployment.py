"""Validate deployment_ratio (revenue/deployment.py) vs Dune 7877551, windowed."""
from __future__ import annotations

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
from drhs.revenue import deployment  # noqa: E402
from run_source import build_source_legs  # noqa: E402

H = {"X-DUNE-API-KEY": os.environ["DUNE_API_KEY"], "Content-Type": "application/json"}
B = "https://api.dune.com/api/v1"
DT_MAX = "2025-12-01"
FILL = date(2025, 11, 30)


def dune(qid: int) -> pd.DataFrame:
    eid = requests.post(f"{B}/query/{qid}/execute", json={}, headers=H, timeout=60).json()["execution_id"]
    while True:
        s = requests.get(f"{B}/execution/{eid}/status", headers=H, timeout=60).json()
        if s["state"] == "QUERY_STATE_COMPLETED":
            break
        if s["state"] in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise RuntimeError(s)
        time.sleep(3)
    rows, off = [], 0
    while True:
        j = requests.get(f"{B}/execution/{eid}/results", params={"limit": 5000, "offset": off},
                         headers=H, timeout=60).json()
        c = j["result"]["rows"]; rows += c; off += len(c)
        if len(c) < 5000:
            break
    return pd.DataFrame(rows)


def main():
    print("computing sp TWA (windowed) ...", flush=True)
    legs = build_source_legs("sp_vaults", date(2025, 12, 1))
    sp = twa.compute_twa(legs, fill_through=FILL)
    dep = deployment.deployment_ratios(sp, end=FILL)
    dep = dep[dep["dt"] < DT_MAX]
    print(f"my deployment rows: {len(dep)}", flush=True)

    dn = dune(7877551)
    dn["dt"] = dn["dt"].astype(str).str[:10]
    dn = dn[dn["dt"] < DT_MAX]
    keys = ["blockchain", "vault_symbol", "dt"]
    m = dep.merge(dn[keys + ["deployment_ratio", "vault_total_supply", "vault_idle_holdings"]],
                  on=keys, how="outer", suffixes=("_hs", "_dn"), indicator=True)
    both = m[m["_merge"] == "both"].copy()
    print(f"matched={len(both)} only_hs={int((m._merge=='left_only').sum())} "
          f"only_dune={int((m._merge=='right_only').sum())}")
    for sym, g in both.groupby("vault_symbol"):
        rd = (g["deployment_ratio_hs"] - g["deployment_ratio_dn"].astype(float)).abs()
        td = (g["vault_total_supply_hs"] - g["vault_total_supply_dn"].astype(float)).abs()
        idl = (g["vault_idle_holdings_hs"] - g["vault_idle_holdings_dn"].astype(float)).abs()
        print(f"[{sym}] n={len(g)} max ratio diff={rd.max():.3e}  max total diff={td.max():.3e}  "
              f"max idle diff={idl.max():.3e}")


if __name__ == "__main__":
    main()
