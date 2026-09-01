"""Worker: compute ONE target-chunk's monthly DR and write its checkpoint CSV.

The monolithic pipeline OOMs the 3.7GB production box, so run_dr_pipeline.py
runs one of these per target in its own subprocess (see
docs/prd-chunked-pipeline.md). Chunking is exact: monthly DR is additive
across disjoint user sets (`monthly_dr` is linear in TWA rows; reclass / rate
/ conversion are row-local), and `--shard k/N` user-hash sharding is exact by
the TWA engine's per-user independence.

The chunk registry is DERIVED from pipeline.SOURCE_MONTHLY x run_source.SPECS
— a new target added to SPECS becomes a chunk automatically
(py/tests/test_chunk_plan.py asserts the 1:1 mapping).

Usage:
    .venv/bin/python py/run_dr_chunk.py <chunk> [--shard k/N]
        [--end 2026-08-01] [--chunks-dir hypersync-results/dr_full]
    .venv/bin/python py/run_dr_chunk.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from drhs import hypersync, twa  # noqa: E402
from drhs.revenue import conversion, deployment, monthly, pipeline  # noqa: E402
from drhs.sources.template_ab import DEFAULT_END  # noqa: E402
from run_source import SPECS, build_source_legs  # noqa: E402

DEFAULT_CHUNKS_DIR = REPO / "hypersync-results" / "dr_full"
# Filename shape of sharded checkpoints — the stale-shard guard and the tests
# must all parse the SAME pattern (import this; never re-declare it).
SHARD_RE = re.compile(r"chunk_(.+)_s(\d+)of(\d+)$")

# Targets too large for one process even with compact legs: (source,
# blockchain, symbol) -> shard count. Tuned from the PRD validation run:
# N=4 peaked at 3,357MB (over the 2.5GB budget; swap-thrashed, 75min/shard) —
# the residual hog is compute_twa's per-row output dicts (8.15M rows/shard at
# N=4). N=8 halves that and measured comfortably inside budget.
SHARDS: dict[tuple[str, str, str], int] = {
    ("susds_psm3", "base", "sUSDS"): 8,
}


def chunk_plan(families: list[str] | None = None) -> dict[str, tuple]:
    """chunk name -> (family, source, target, shard_n | None)."""
    plan: dict[str, tuple] = {}
    for family, (srcs, _re, _cv, _sp) in pipeline.SOURCE_MONTHLY.items():
        if families is not None and family not in families:
            continue
        for src in srcs:
            for t in SPECS[src].targets:
                name = f"{src}_{t.blockchain}_{t.symbol}"
                if name in plan:
                    raise ValueError(f"duplicate chunk name {name}")
                plan[name] = (family, src, t, SHARDS.get((src, t.blockchain, t.symbol)))
    return plan


def check_archive_coverage(chains, end: date) -> None:
    """Refuse to scan a window the HyperSync archives have not fully indexed
    yet: a head behind ``end`` silently truncates the scan at a DIFFERENT
    effective cutoff as the head advances mid-run (observed on the Aug-2026
    settlement: the eth head was ~20h behind month-end at launch). Called by
    the orchestrator for the chains it is about to scan, and by this worker
    for its own chain — so a documented standalone chunk rerun gets the same
    protection as an orchestrated launch."""
    end_ts = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp())
    behind = []
    for chain in sorted(set(chains)):
        _blk, ts = hypersync.returnable_head(chain)
        if ts < end_ts:
            behind.append(
                f"{chain} (head {datetime.fromtimestamp(ts, tz=timezone.utc):%Y-%m-%d %H:%M}Z)")
    if behind:
        raise SystemExit(
            f"archives not caught up to end={end}: {', '.join(behind)} — "
            "wait for the archive heads to pass the window end, then rerun")


def chunk_csv(chunks_dir: Path, name: str, shard: str | None) -> Path:
    suffix = f"_s{shard.replace('/', 'of')}" if shard else ""
    return chunks_dir / f"chunk_{name}{suffix}.csv"


def parse_shard(shard: str, plan_n: int | None) -> tuple[int, int]:
    """Validate a k/N shard spec: 0 <= k < N, and N must match the plan."""
    try:
        k, n = (int(x) for x in shard.split("/"))
    except ValueError:
        raise SystemExit(f"bad --shard {shard!r} (expected k/N)")
    if not (n > 0 and 0 <= k < n):
        raise SystemExit(f"bad --shard {shard!r}: need 0 <= k < N (shards are 0-based)")
    if plan_n is not None and n != plan_n:
        raise SystemExit(f"--shard {shard!r} does not match the plan's N={plan_n}")
    return k, n


# --- chunks-dir manifest -------------------------------------------------------
# Checkpoints are only reusable for the SAME scan window: the manifest pins the
# --end they were built with, and both worker and orchestrator refuse to mix
# windows (resume with a different --end would silently reuse stale months).
def ensure_manifest(chunks_dir: Path, end: date) -> None:
    mf = chunks_dir / "manifest.json"
    if mf.exists():
        have = json.loads(mf.read_text()).get("end")
        if have != end.isoformat():
            raise SystemExit(
                f"{chunks_dir} holds checkpoints for end={have}, requested "
                f"end={end.isoformat()} — rerun with --fresh or a different --chunks-dir")
        return
    chunks_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(mf, json.dumps({"end": end.isoformat()}))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def load_chunks(chunks_dir: Path, expected: list[Path] | None = None):
    """Read checkpoint CSVs and sum to (month, blockchain, token, ref_code,
    source) — the ONE combine used by the pipeline and the workbook builder.

    With ``expected`` (the orchestrator's plan): every expected file must
    exist and NO other chunk_*.csv may be present — a stray file (legacy
    naming, an unsharded checkpoint next to its shard set) would silently
    double count, so it is a hard error. Without ``expected`` (workbook
    builder on an already-validated dir): all files are read, guarded against
    mixed shard-N families.
    """
    import pandas as pd
    files = sorted(chunks_dir.glob("chunk_*.csv"))
    if expected is not None:
        exp = {p.resolve() for p in expected}
        strays = [c.name for c in files if c.resolve() not in exp]
        missing = [p.name for p in expected if not p.exists()]
        if strays or missing:
            raise SystemExit(
                f"chunks dir {chunks_dir} does not match the plan — "
                f"strays (would double count): {strays or '-'}; missing: {missing or '-'}")
        files = sorted(exp)
    fam: dict[str, set[str]] = {}
    for c in files:
        m = SHARD_RE.match(c.stem)
        if m:
            fam.setdefault(m.group(1), set()).add(m.group(3))
    mixed = {b: ns for b, ns in fam.items() if len(ns) > 1}
    if mixed:
        raise SystemExit(f"mixed shard families would double count: {mixed}")
    df = pd.concat([pd.read_csv(c) for c in files], ignore_index=True)
    return (df.groupby(["month", "blockchain", "token", "ref_code", "source"])["dr_usd"]
            .sum().reset_index())


def build_target_legs(src: str, t, end: date):
    """Legs for ONE target — build_source_legs with a target override, so the
    SourceSpec wiring (exclusions, synthetic programs, re-routes) has exactly
    one home."""
    return build_source_legs(src, end, targets=[t])


def compute_chunk(name: str, shard: str | None, end: date):
    family, src, t, plan_n = chunk_plan()[name]
    _srcs, reclass, conv_builder, is_sp = pipeline.SOURCE_MONTHLY[family]
    if shard is not None and is_sp:
        # deployment_ratios needs the FULL vault TWA (idle series is the whole
        # chain state); a shard's partial supply yields wrong, often 0, ratios.
        raise SystemExit(f"sharding sp sources is not exact — refuse {name}")
    # end is EXCLUSIVE, the fill day INCLUSIVE: fill through the day before
    # min(end, DEFAULT_END), so a windowed rerun (--end 2026-07-01) reproduces
    # the settled June numbers instead of leaking one day of the next month.
    fill = min(end, DEFAULT_END) - timedelta(days=1)

    legs = build_target_legs(src, t, end)
    if shard is not None:
        k, n = parse_shard(shard, plan_n)
        legs = legs[legs["user_addr"].map(lambda u: int(u[2:10], 16) % n == k)].copy()
    print(f"[{name}{'/' + shard if shard else ''}] {len(legs)} legs; TWA ...", flush=True)
    tw = twa.compute_twa(legs, fill_through=fill)
    del legs
    print(f"[{name}] {len(tw)} TWA rows; monthly ...", flush=True)

    if is_sp:
        dep = deployment.deployment_ratios(tw, end=fill)
        dep_map = {(r.blockchain, r.vault_symbol, r.dt): r.deployment_ratio
                   for r in dep.itertuples()}
        m = monthly.monthly_dr(tw, reclassify=reclass,
                               conv_lookup=monthly.sp_conv(conversion.sp_vault_rates()),
                               sp_deployment=dep_map)
    else:
        m = monthly.monthly_dr(tw, reclassify=reclass, conv_lookup=conv_builder())
    m["source"] = family
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chunk", nargs="?", help="chunk name (see --list)")
    ap.add_argument("--shard", default=None, help="k/N user-hash shard")
    ap.add_argument("--end", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                    default=DEFAULT_END)
    ap.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS_DIR)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or not args.chunk:
        for name, (family, _s, _t, n) in chunk_plan().items():
            print(f"{name:40s} family={family}" + (f"  shards={n}" if n else ""))
        return 0

    if args.end > DEFAULT_END:
        raise SystemExit(
            f"--end {args.end} is beyond the deployed scan cutoff {DEFAULT_END}: every "
            "source caps its window there, so later months would be silently empty. "
            "Extend the settlement window first (bump DEFAULT_END in drhs/window.py — "
            "the single home; the fill and conversion caps derive from it).")
    ensure_manifest(args.chunks_dir, args.end)
    out = chunk_csv(args.chunks_dir, args.chunk, args.shard)
    if out.exists():
        print(f"[{args.chunk}] {out.name} exists, skipping")
        return 0
    check_archive_coverage([chunk_plan()[args.chunk][2].blockchain], args.end)
    m = compute_chunk(args.chunk, args.shard, args.end)
    # atomic checkpoint: a kill mid-write must never leave a truncated CSV
    # that a resume would accept as complete.
    tmp = out.with_suffix(".csv.tmp")
    m.to_csv(tmp, index=False)
    os.replace(tmp, out)
    import resource
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(f"[{args.chunk}] wrote {out} ({len(m)} rows; peak RSS {peak_mb}MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
