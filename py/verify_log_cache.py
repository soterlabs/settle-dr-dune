"""Audit the persistent log cache against the live archives.

For every cache entry (or one chain's), sample K random block sub-ranges from
the cached coverage, re-fetch them live, and require the row sets to match
exactly — order-independent, every field compared. Logs are immutable, so any
difference means a corrupt/stale cache entry: delete the entry dir and let
the next run re-fetch.

    .venv/bin/python py/verify_log_cache.py [--chain base] [--samples 5]
        [--span 50000] [--seed N]

Exit 0 = every sampled range matches; 1 = at least one mismatch.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from drhs import logcache  # noqa: E402
from drhs.hypersync import _cache_dir, _query_logs_live  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain", default=None, help="restrict to one chain")
    ap.add_argument("--samples", type=int, default=5, help="ranges per entry")
    ap.add_argument("--span", type=int, default=50_000, help="blocks per sample")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    root = _cache_dir() / "logs"
    entries = sorted(p for p in root.glob("*/*/meta.json")
                     if args.chain is None or p.parent.parent.name == args.chain)
    if not entries:
        print(f"no cache entries under {root}" + (f" for chain {args.chain}" if args.chain else ""))
        return 0

    bad = 0
    for mf in entries:
        d = mf.parent
        chain = d.parent.name
        meta = logcache.load_meta(d)
        if meta is None:
            print(f"[{chain}/{d.name}] BAD META (gap/overlap/missing file) — delete {d}")
            bad += 1
            continue
        sels = json.loads(json.dumps(meta.selections))  # verbatim spec from meta
        width = meta.cached_through - meta.cached_from + 1
        span = min(args.span, width)
        for i in range(args.samples):
            lo = rng.randint(meta.cached_from, meta.cached_through - span + 1)
            hi = lo + span - 1
            cached = logcache.read_rows(d, meta, lo, hi)
            live = _query_logs_live(chain, sels, lo, hi, log_fields=meta.log_fields,
                                    with_tx_to=meta.with_tx_to).rows
            import dataclasses
            ok = sorted(map(dataclasses.astuple, cached)) \
                == sorted(map(dataclasses.astuple, live))
            tag = "OK" if ok else "MISMATCH"
            print(f"[{chain}/{d.name}] sample {i + 1}/{args.samples} "
                  f"blocks [{lo},{hi}]: cached={len(cached)} live={len(live)} {tag}")
            if not ok:
                bad += 1
    print(f"\n{'ALL SAMPLES MATCH' if not bad else f'{bad} MISMATCH(ES) — delete the flagged entry dirs'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
