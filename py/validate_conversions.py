"""Validate the event-derived conversion rates vs Dune 7877548/549/550."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")
from drhs.revenue import conversion  # noqa: E402

H = {"X-DUNE-API-KEY": os.environ["DUNE_API_KEY"], "Content-Type": "application/json"}
B = "https://api.dune.com/api/v1"


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


def cmp_series(name, mine, dn, ratecol):
    mine = mine.copy(); mine["dt"] = mine["dt"].astype(str).str[:10]
    dn = dn.copy(); dn["dt"] = dn["dt"].astype(str).str[:10]
    m = mine.merge(dn[["dt", ratecol]], on="dt", how="outer", indicator=True)
    both = m[m["_merge"] == "both"]
    d = (both["rate"] - both[ratecol].astype(float)).abs()
    print(f"[{name}] my={len(mine)} dune={len(dn)} matched={len(both)} "
          f"only_mine={int((m._merge=='left_only').sum())} only_dune={int((m._merge=='right_only').sum())}")
    print(f"    max abs rate diff={d.max():.3e}  max rel={ (d/both[ratecol].astype(float).abs().clip(lower=1e-18)).max():.3e}")


def main():
    print("fetching Dune conversions ...", flush=True)
    cmp_series("susds", conversion.susds_rates(), dune(7877548), "susds_conversion_rate")
    cmp_series("stusds", conversion.stusds_rates(), dune(7877549), "stusds_conversion_rate")
    sp_mine = conversion.sp_vault_rates()
    sp_dn = dune(7877550)
    for (chain, sym), g in sp_mine.groupby(["blockchain", "token_symbol"]):
        gd = sp_dn[(sp_dn["blockchain"] == chain) & (sp_dn["token_symbol"] == sym)]
        cmp_series(f"sp {sym}/{chain}", g.rename(columns={"conversion_rate": "rate"}), gd, "conversion_rate")


if __name__ == "__main__":
    main()
