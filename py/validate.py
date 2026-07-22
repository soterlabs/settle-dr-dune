"""Validate a HyperSync TWA source against its Dune foundational query.

    .venv/bin/python py/validate.py <source> --query <dune_id> [--end YYYY-MM-DD]

Runs the source over HyperSync, executes the Dune query for the same window,
filters the Dune output to the (blockchain, contract_address) pairs the source
covers, and diffs per (blockchain, contract_address, user_addr, dt, ref_code) —
the TWA value plus day_type / segment_duration_seconds.

Dune query ids (foundational Template A/B):
  stusds     -> 7877544   (stUSDS only)
  susds_eth  -> 7877542   (twa_susds_susdc_erc4626; filtered to sUSDS/eth)
  susdc      -> 7877542   (filtered to the sUSDC contracts)

Env: ENVIO_API_TOKEN + DUNE_API_KEY (repo-root .env).
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
from run_source import SOURCES, SOURCE_EXCLUDED  # noqa: E402

DUNE_BASE = "https://api.dune.com/api/v1"
DEFAULT_QUERY = {"stusds": 7877544, "susds_eth": 7877542, "susdc": 7877542}


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _headers() -> dict:
    return {"X-DUNE-API-KEY": os.environ["DUNE_API_KEY"], "Content-Type": "application/json"}


def _req(method: str, url: str, **kw) -> dict:
    for attempt in range(9):
        res = requests.request(method, url, headers=_headers(), timeout=90, **kw)
        if res.status_code == 429 or res.status_code >= 500:
            wait = min(60, 2 ** attempt)
            print(f"[dune] {res.status_code}; backing off {wait}s", flush=True)
            time.sleep(wait)
            continue
        res.raise_for_status()
        return res.json()
    raise RuntimeError(f"Dune {method} {url}: repeated {res.status_code}s")


def run_dune(query_id: int, end: date, filters: str | None = None) -> pd.DataFrame:
    end_param = f"{end.isoformat()} 00:00:00"
    print(f"[dune] executing query {query_id} end_date={end_param} ...", flush=True)
    j = _req("POST", f"{DUNE_BASE}/query/{query_id}/execute",
             json={"query_parameters": {"end_date": end_param}})
    exec_id = j["execution_id"]
    print(f"[dune] execution_id={exec_id}; polling ...", flush=True)
    while True:
        s = _req("GET", f"{DUNE_BASE}/execution/{exec_id}/status")
        state = s["state"]
        if state == "QUERY_STATE_COMPLETED":
            print(f"[dune] total_row_count="
                  f"{s.get('result_metadata',{}).get('total_row_count')}", flush=True)
            break
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise RuntimeError(f"Dune execution {state}: {s}")
        time.sleep(5)
    rows: list[dict] = []
    offset = 0
    page = 5000
    cols = ("blockchain,contract_address,symbol,user_addr,dt,ref_code,"
            "time_weighted_avg_balance,day_type,"
            "segment_duration_seconds,segment_balance_time_product")
    # offset pagination over a `filters`ed view is only stable with an explicit
    # sort — without it Dune re-returns overlapping rows (massive duplication).
    base = {"limit": page, "columns": cols, "sort_by": "user_addr,dt,ref_code"}
    if filters:
        base["filters"] = filters
        print(f"[dune] server-side filter: {filters}", flush=True)
    while True:
        j = _req("GET", f"{DUNE_BASE}/execution/{exec_id}/results",
                 params={**base, "offset": offset})
        chunk = j["result"]["rows"]
        rows.extend(chunk)
        offset += len(chunk)
        if len(chunk) < page:
            break
        time.sleep(1.0)
    print(f"[dune] downloaded {len(rows)} rows", flush=True)
    return pd.DataFrame(rows)


def _norm_dt(s) -> str:
    return str(s)[:10]


KEYS = ["k_chain", "k_contract", "k_user", "k_dt", "k_ref"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=sorted(SOURCES))
    ap.add_argument("--query", type=int, default=None, help="Dune query id (default per source)")
    ap.add_argument("--end", type=_parse_date, default=template_ab.DEFAULT_END)
    ap.add_argument("--dt-max", type=_parse_date, default=None,
                    help="compare only rows with dt < this (default: --end). Excludes the "
                         "flat no-transaction-day fill tail beyond the scan window, keeping "
                         "the Dune download small for large tokens. Set to 2026-07-01 to "
                         "compare the full filled history.")
    ap.add_argument("--tol", type=float, default=1e-6)
    args = ap.parse_args()
    dt_max = args.dt_max or args.end
    query_id = args.query or DEFAULT_QUERY.get(args.source)
    if query_id is None:
        print(f"no default Dune query for {args.source!r}; pass --query")
        return 2

    print(f"[hs] computing {args.source} TWA end={args.end} ...", flush=True)
    legs = template_ab.build_legs(
        SOURCES[args.source], end_date=args.end,
        excluded=SOURCE_EXCLUDED.get(args.source, frozenset()))
    hs = twa.compute_twa(legs)
    if hs.empty:
        print("[hs] EMPTY — nothing to validate (source has no data in window).")
        return 1
    hs["k_chain"] = hs["blockchain"]
    hs["k_contract"] = hs["contract_address"].str.lower()
    hs["k_user"] = hs["user_addr"].str.lower()
    hs["k_dt"] = hs["dt"].map(_norm_dt)
    hs["k_ref"] = hs["ref_code"].astype(int)
    dt_max_s = dt_max.isoformat()
    hs = hs[hs["k_dt"] < dt_max_s].copy()
    covered = set(zip(hs["k_chain"], hs["k_contract"]))
    symbols = sorted(hs["symbol"].unique())
    print(f"[hs] {len(hs)} rows (dt < {dt_max_s}) over {len(covered)} (chain, contract) "
          f"pairs, symbols={symbols}", flush=True)

    sym_clause = " or ".join(f"symbol = '{s}'" for s in symbols)
    filters = f"({sym_clause}) and dt < '{dt_max_s}'"
    dn = run_dune(query_id, args.end, filters=filters)
    if dn.empty:
        print("[dune] EMPTY result — cannot validate.")
        return 1
    dn["k_chain"] = dn["blockchain"]
    dn["k_contract"] = dn["contract_address"].str.lower()
    dn["k_user"] = dn["user_addr"].str.lower()
    dn["k_dt"] = dn["dt"].map(_norm_dt)
    dn["k_ref"] = dn["ref_code"].astype(float).astype(int)
    dn = dn[[tuple(x) in covered for x in zip(dn["k_chain"], dn["k_contract"])]].copy()
    dn = dn[dn["k_dt"] < dt_max_s].copy()
    before = len(dn)
    dn = dn.drop_duplicates(subset=KEYS).copy()
    if len(dn) != before:
        print(f"[dune] WARNING: dropped {before-len(dn)} duplicate rows (pagination)", flush=True)
    print(f"[dune] {len(dn)} rows after filtering to the source's contracts", flush=True)
    dn["dn_twab"] = dn["time_weighted_avg_balance"].astype(float)
    dn["dn_daytype"] = dn["day_type"]
    dn["dn_segdur"] = pd.to_numeric(dn["segment_duration_seconds"], errors="coerce")

    hs_k = hs[KEYS + ["time_weighted_avg_balance", "day_type",
                      "segment_duration_seconds"]].rename(
        columns={"time_weighted_avg_balance": "hs_twab", "day_type": "hs_daytype",
                 "segment_duration_seconds": "hs_segdur"})
    dn_k = dn[KEYS + ["dn_twab", "dn_daytype", "dn_segdur"]]
    m = hs_k.merge(dn_k, on=KEYS, how="outer", indicator=True)

    both = m[m["_merge"] == "both"].copy()
    only_hs = m[m["_merge"] == "left_only"]
    only_dn = m[m["_merge"] == "right_only"]
    both["absdiff"] = (both["hs_twab"] - both["dn_twab"]).abs()

    print(f"\n==== {args.source} TWA validation (end {args.end}) ====")
    print(f"rows: HyperSync={len(hs_k)}  Dune={len(dn_k)}  matched keys={len(both)}")
    print(f"only in HyperSync: {len(only_hs)}   only in Dune: {len(only_dn)}")
    within = (both["absdiff"] <= args.tol).sum()
    print(f"[TWA] within abs tol {args.tol}: {within}/{len(both)} "
          f"({100*within/max(len(both),1):.3f}%)  max absdiff={both['absdiff'].max():.3e}")
    print(f"[day_type] mismatches: "
          f"{int((both['hs_daytype']!=both['dn_daytype']).sum())}/{len(both)}")
    tx = both[both["dn_daytype"] == "transaction_day"]
    segbad = int(((tx["hs_segdur"] - tx["dn_segdur"]).abs() > 0.5).sum())
    print(f"[segment_duration] exact: {len(tx)-segbad}/{len(tx)}")

    agg_hs, agg_dn = hs["time_weighted_avg_balance"].sum(), dn["dn_twab"].sum()
    print(f"Σ TWA  HyperSync={agg_hs:.6f}  Dune={agg_dn:.6f}  "
          f"reldiff={abs(agg_hs-agg_dn)/max(abs(agg_dn),1e-18):.3e}")
    unm = m[m["_merge"] != "both"]
    if len(unm):
        umax = pd.concat([only_hs["hs_twab"], only_dn["dn_twab"]]).abs().max()
        print(f"unmatched keys: {len(unm)}  (max |TWA| among them = {umax:.3e})")
    if len(both):
        worst = both.sort_values("absdiff", ascending=False).head(5)
        print("worst abs diffs:")
        print(worst[KEYS + ["hs_twab", "dn_twab", "absdiff"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
