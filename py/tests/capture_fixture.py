"""Capture an offline parity fixture for a token+window.

    .venv/bin/python py/tests/capture_fixture.py <source> --end YYYY-MM-DD

Writes py/tests/fixtures/<source>_<end>/:
  meta.json                         source, end, excluded addresses
  <chain>_<addr>.referrals.json     raw Referral LogRows for each target
  <chain>_<addr>.transfers.json     raw Transfer LogRows for each target
  dune_golden.csv                   Dune TWA output (this source's contracts, dt < end)

test_parity.py replays the pipeline on the raw rows (no network) and asserts it
still reproduces dune_golden.csv — locking in the Dune parity achieved when the
fixture was captured, so future changes can't silently break it.

Run this ONCE per token after its live Dune validation passes; commit the
fixture. Requires ENVIO_API_TOKEN + DUNE_API_KEY.
"""

from __future__ import annotations

import argparse
import dataclasses
import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT / "py"))
load_dotenv(ROOT / ".env")

from run_source import SPECS  # noqa: E402
from validate import run_dune  # noqa: E402

FIX_DIR = Path(__file__).parent / "fixtures"


def _write_gz(path: Path, obj) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(obj, f)


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", choices=sorted(SPECS))
    ap.add_argument("--end", type=_parse_date, required=True)
    ap.add_argument("--query", type=int, default=None)
    args = ap.parse_args()
    spec = SPECS[args.source]
    query_id = args.query or spec.dune_query
    end_ts = spec.template._end_ts(args.end)
    end_s = args.end.isoformat()

    out = FIX_DIR / f"{args.source}_{end_s}"
    out.mkdir(parents=True, exist_ok=True)
    targets = spec.targets
    excluded = sorted(spec.excluded)

    covered = set()
    for t in targets:
        print(f"[capture] fetching {t.symbol} {t.blockchain} {t.address} ...", flush=True)
        ref_rows, tr_rows = spec.template.fetch_target_rows(t, end_ts)
        tag = f"{t.blockchain}_{t.address.lower()}"
        _write_gz(out / f"{tag}.{spec.ref_kind}.json.gz", [dataclasses.asdict(r) for r in ref_rows])
        _write_gz(out / f"{tag}.transfers.json.gz", [dataclasses.asdict(r) for r in tr_rows])
        covered.add((t.blockchain, t.address.lower()))
        print(f"  {len(ref_rows)} {spec.ref_kind}, {len(tr_rows)} transfers", flush=True)

    # Dune golden (server-side filtered to symbols + dt < end).
    symbols = sorted({t.symbol for t in targets})
    chains = sorted({t.blockchain for t in targets})
    sym_clause = " or ".join(f"symbol = '{s}'" for s in symbols)
    chain_clause = " or ".join(f"blockchain = '{c}'" for c in chains)
    filters = f"({sym_clause}) and ({chain_clause}) and dt < '{end_s}'"
    dn = run_dune(query_id, args.end, filters=filters)
    dn["_c"] = list(zip(dn["blockchain"], dn["contract_address"].str.lower()))
    dn = dn[[c in covered for c in dn["_c"]]].drop(columns="_c")
    dn = dn[dn["dt"].map(lambda s: str(s)[:10]) < end_s]
    dn = dn.drop_duplicates(
        subset=["blockchain", "contract_address", "user_addr", "dt", "ref_code"])
    keep = ["blockchain", "contract_address", "symbol", "user_addr", "dt", "ref_code",
            "time_weighted_avg_balance", "day_type",
            "segment_duration_seconds", "segment_balance_time_product"]
    dn[keep].to_csv(out / "dune_golden.csv.gz", index=False, compression="gzip")

    (out / "meta.json").write_text(json.dumps({
        "source": args.source, "end": end_s, "query_id": query_id,
        "excluded": excluded, "dune_rows": int(len(dn)),
    }, indent=2))
    print(f"[capture] wrote fixture {out} ({len(dn)} golden rows)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
