"""Full-history stUSDS parity at (month, ref_code) grain — quota-efficient.

Bulk per-row download of query_7877544 (112k rows) exceeds the primary Dune
key's datapoint quota. Instead we create a tiny aggregation query over the
public query_7877544 that returns count + Σ TWA per (month, ref_code) — a few
hundred rows — and diff it against the HyperSync full-history output aggregated
identically. This localizes any row-count gap (which month/ref_code) and shows
whether the extra rows are dust (Σ matches) or material (Σ diverges).

Uses the alternate Dune key from ../settlement-cycle/.env (separate quota).
"""

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

# Primary (openmsc) key — owns query_7877544 and can create queries (paid plan).
# Query creation costs no datapoints; the tiny aggregate result costs few, so
# it fits under the download quota even after the per-row runs exhausted it.
B = "https://api.dune.com/api/v1"
H = {"X-DUNE-API-KEY": os.environ["DUNE_API_KEY"], "Content-Type": "application/json"}

AGG_SQL = """
select cast(date_trunc('month', dt) as date) as m,
       ref_code,
       count(*) as n,
       cast(sum(time_weighted_avg_balance) as double) as s
from query_7877544
group by 1, 2
order by 1, 2
""".strip()


def _req(method, url, **kw):
    for attempt in range(9):
        r = requests.request(method, url, headers=H, timeout=90, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(min(60, 2 ** attempt))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError(f"{method} {url}: {r.status_code} {r.text[:200]}")


def dune_agg() -> pd.DataFrame:
    q = _req("POST", f"{B}/query", json={
        "name": "TMP validate stUSDS agg (HyperSync parity)",
        "query_sql": AGG_SQL, "is_private": True,
    })
    qid = q["query_id"]
    print(f"[dune] created agg query {qid}; executing ...", flush=True)
    try:
        ex = _req("POST", f"{B}/query/{qid}/execute", json={"query_parameters": {}})
        eid = ex["execution_id"]
        while True:
            s = _req("GET", f"{B}/execution/{eid}/status")
            st = s["state"]
            if st == "QUERY_STATE_COMPLETED":
                break
            if st in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
                raise RuntimeError(f"exec {st}: {s}")
            time.sleep(4)
        j = _req("GET", f"{B}/execution/{eid}/results", params={"limit": 5000})
        rows = j["result"]["rows"]
        print(f"[dune] agg rows: {len(rows)}", flush=True)
        return pd.DataFrame(rows)
    finally:
        # archive the temp query so it doesn't clutter the account
        try:
            _req("POST", f"{B}/query/{qid}/archive")
            print(f"[dune] archived temp query {qid}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[dune] could not archive {qid}: {e}", flush=True)


def main() -> int:
    hs = pd.read_csv(ROOT / "hypersync-results" / "twa_stusds.csv")
    hs["m"] = hs["dt"].str[:7]
    hs_agg = hs.groupby(["m", "ref_code"]).agg(
        hs_n=("time_weighted_avg_balance", "size"),
        hs_s=("time_weighted_avg_balance", "sum"),
    ).reset_index()

    dn = dune_agg()
    dn["m"] = dn["m"].astype(str).str[:7]
    dn["ref_code"] = dn["ref_code"].astype(float).astype(int)
    dn = dn.rename(columns={"n": "dn_n", "s": "dn_s"})[["m", "ref_code", "dn_n", "dn_s"]]

    m = hs_agg.merge(dn, on=["m", "ref_code"], how="outer", indicator=True).fillna(
        {"hs_n": 0, "dn_n": 0, "hs_s": 0.0, "dn_s": 0.0})
    m["n_diff"] = m["hs_n"].astype(int) - m["dn_n"].astype(int)
    m["s_absdiff"] = (m["hs_s"] - m["dn_s"]).abs()
    m["s_reldiff"] = m["s_absdiff"] / m["dn_s"].abs().clip(lower=1e-18)

    print("\n==== stUSDS FULL-HISTORY parity (month, ref_code) ====")
    print(f"total rows  HyperSync={int(m['hs_n'].sum())}  Dune={int(m['dn_n'].sum())}  "
          f"net row diff={int(m['hs_n'].sum()-m['dn_n'].sum())}")
    print(f"Σ TWA  HyperSync={m['hs_s'].sum():.6f}  Dune={m['dn_s'].sum():.6f}  "
          f"reldiff={abs(m['hs_s'].sum()-m['dn_s'].sum())/max(abs(m['dn_s'].sum()),1e-18):.3e}")
    print(f"cells: total={len(m)}  count-exact={int((m['n_diff']==0).sum())}  "
          f"Σ-within-1e-3-rel={int((m['s_reldiff']<=1e-3).sum())}")
    bad = m[m["n_diff"] != 0].sort_values("n_diff", key=lambda s: s.abs(), ascending=False)
    if len(bad):
        print(f"\ncells with row-count gaps ({len(bad)}):")
        print(bad[["m", "ref_code", "hs_n", "dn_n", "n_diff", "hs_s", "dn_s", "s_reldiff"]]
              .head(20).to_string(index=False))
    sbad = m[m["s_reldiff"] > 1e-6].sort_values("s_reldiff", ascending=False)
    print(f"\ncells with Σ-TWA rel diff > 1e-6: {len(sbad)}")
    if len(sbad):
        print(sbad[["m", "ref_code", "hs_n", "dn_n", "hs_s", "dn_s", "s_reldiff"]]
              .head(20).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
