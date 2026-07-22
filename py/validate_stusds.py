"""Validate the HyperSync stUSDS TWA against Dune query 7877544 for a window.

    .venv/bin/python py/validate_stusds.py --end 2025-10-01

Executes the Dune query with the same end_date, paginates the result, and diffs
per (user_addr, dt, ref_code) against a freshly-computed HyperSync TWA for the
identical window. Reports row coverage and the balance-difference distribution.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")

from drhs import twa  # noqa: E402
from drhs.sources import template_ab  # noqa: E402

DUNE_QUERY_ID = 7877544
DUNE_BASE = "https://api.dune.com/api/v1"


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _headers() -> dict:
    key = os.environ["DUNE_API_KEY"]
    return {"X-DUNE-API-KEY": key, "Content-Type": "application/json"}


def _req(method: str, url: str, **kw) -> dict:
    """Dune request with backoff on 429 / transient errors."""
    h = _headers()
    for attempt in range(9):
        res = requests.request(method, url, headers=h, timeout=60, **kw)
        if res.status_code == 429 or res.status_code >= 500:
            wait = min(60, 2 ** attempt)
            print(f"[dune] {res.status_code}; backing off {wait}s", flush=True)
            time.sleep(wait)
            continue
        res.raise_for_status()
        return res.json()
    raise RuntimeError(f"Dune {method} {url}: repeated {res.status_code}s")


def run_dune(end: date) -> pd.DataFrame:
    end_param = f"{end.isoformat()} 00:00:00"
    print(f"[dune] executing query {DUNE_QUERY_ID} end_date={end_param} ...", flush=True)
    j = _req("POST", f"{DUNE_BASE}/query/{DUNE_QUERY_ID}/execute",
             json={"query_parameters": {"end_date": end_param}})
    exec_id = j["execution_id"]
    print(f"[dune] execution_id={exec_id}; polling ...", flush=True)
    while True:
        s = _req("GET", f"{DUNE_BASE}/execution/{exec_id}/status")
        state = s["state"]
        if state == "QUERY_STATE_COMPLETED":
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise RuntimeError(f"Dune execution {state}: {s}")
        time.sleep(5)
    rows: list[dict] = []
    offset = 0
    page = 5000
    while True:
        j = _req("GET", f"{DUNE_BASE}/execution/{exec_id}/results",
                 params={"limit": page, "offset": offset})
        chunk = j["result"]["rows"]
        rows.extend(chunk)
        offset += len(chunk)
        if len(chunk) < page:
            break
        time.sleep(1.0)
    print(f"[dune] {len(rows)} rows", flush=True)
    return pd.DataFrame(rows)


def _norm_dt(s) -> str:
    return str(s)[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", type=_parse_date, default=date(2025, 10, 1))
    ap.add_argument("--tol", type=float, default=1e-6, help="abs tolerance on TWA")
    args = ap.parse_args()

    # HyperSync side, same window.
    print(f"[hs] computing HyperSync stUSDS TWA end={args.end} ...", flush=True)
    legs = template_ab.build_legs([template_ab.STUSDS], end_date=args.end)
    hs = twa.compute_twa(legs)
    hs["k_dt"] = hs["dt"].map(_norm_dt)
    hs["k_user"] = hs["user_addr"].str.lower()
    hs["k_ref"] = hs["ref_code"].astype(int)

    dn = run_dune(args.end)
    if dn.empty:
        print("[dune] EMPTY result — cannot validate.")
        return 1
    dn["k_dt"] = dn["dt"].map(_norm_dt)
    dn["k_user"] = dn["user_addr"].str.lower()
    dn["k_ref"] = dn["ref_code"].astype(float).astype(int)
    dn["dn_twab"] = dn["time_weighted_avg_balance"].astype(float)

    keys = ["k_user", "k_dt", "k_ref"]
    hs_k = hs[keys + ["time_weighted_avg_balance"]].rename(
        columns={"time_weighted_avg_balance": "hs_twab"})
    dn_k = dn[keys + ["dn_twab"]]
    m = hs_k.merge(dn_k, on=keys, how="outer", indicator=True)

    both = m[m["_merge"] == "both"].copy()
    only_hs = m[m["_merge"] == "left_only"]
    only_dn = m[m["_merge"] == "right_only"]
    both["absdiff"] = (both["hs_twab"] - both["dn_twab"]).abs()
    both["reldiff"] = both["absdiff"] / both["dn_twab"].abs().clip(lower=1e-18)

    print("\n==== stUSDS TWA validation ====")
    print(f"window: 2024-09-01 .. {args.end} (exclusive)")
    print(f"rows: HyperSync={len(hs_k)}  Dune={len(dn_k)}  matched keys={len(both)}")
    print(f"only in HyperSync: {len(only_hs)}   only in Dune: {len(only_dn)}")
    within = (both["absdiff"] <= args.tol).sum()
    print(f"matched within abs tol {args.tol}: {within}/{len(both)} "
          f"({100*within/max(len(both),1):.3f}%)")
    print(f"max absdiff={both['absdiff'].max():.3e}  "
          f"median absdiff={both['absdiff'].median():.3e}  "
          f"max reldiff={both['reldiff'].max():.3e}")

    agg_hs = hs["time_weighted_avg_balance"].sum()
    agg_dn = dn["dn_twab"].sum()
    print(f"\nΣ TWA  HyperSync={agg_hs:.6f}  Dune={agg_dn:.6f}  "
          f"reldiff={abs(agg_hs-agg_dn)/max(abs(agg_dn),1e-18):.3e}")

    for label, frame in (("HyperSync-only", only_hs), ("Dune-only", only_dn)):
        if len(frame):
            print(f"\nsample {label} keys:")
            print(frame.head(8).to_string(index=False))
    if len(both):
        worst = both.sort_values("absdiff", ascending=False).head(8)
        print("\nworst abs diffs:")
        print(worst[keys + ["hs_twab", "dn_twab", "absdiff"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
