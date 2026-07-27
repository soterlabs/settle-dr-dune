"""Run the full DR pipeline off HyperSync (no Dune) and write the rollups.

    .venv/bin/python py/run_dr_pipeline.py [--sources stusds,farms] [--end 2026-07-01]

Writes to hypersync-results/dr/:
    dr_monthly_combined.csv
    dr_rollup_by_refcode.csv
    dr_rollup_by_refcode_token.csv

Default mode is CHUNKED (docs/prd-chunked-pipeline.md): one target per
subprocess (worker: run_dr_chunk.py), sequential, with checkpoint CSVs under
--chunks-dir — a killed run resumes by skipping completed chunks; oversized
targets are user-hash sharded (exact by per-user independence). The
monolithic in-process path (--monolithic) remains for small source subsets
and debugging, but OOMs the 3.7GB production box on a full run.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")

import pandas as pd  # noqa: E402

from drhs.revenue import pipeline  # noqa: E402
from run_dr_chunk import chunk_csv, chunk_plan  # noqa: E402
from run_source import build_source_legs  # noqa: E402


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _run_chunked(families: list[str], args) -> int:
    plan = chunk_plan(families)
    jobs: list[tuple[str, str | None, Path]] = []
    for name, (_f, _s, _t, n) in plan.items():
        for shard in ([None] if not n else [f"{k}/{n}" for k in range(n)]):
            jobs.append((name, shard, chunk_csv(args.chunks_dir, name, shard)))

    if args.list:
        for name, shard, csv in jobs:
            state = "done" if csv.exists() else "pending"
            print(f"{csv.name:55s} {state}")
        return 0

    failed: list[str] = []
    for name, shard, csv in jobs:
        if csv.exists() and not args.fresh:
            print(f"[dr] {csv.name} exists, skipping", flush=True)
            continue
        if args.fresh and csv.exists():
            csv.unlink()
        cmd = [sys.executable, "-u", str(Path(__file__).parent / "run_dr_chunk.py"),
               name, "--end", args.end.isoformat(), "--chunks-dir", str(args.chunks_dir)]
        if shard:
            cmd += ["--shard", shard]
        print(f"[dr] chunk {name}{'/' + shard if shard else ''} ...", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            print(f"[dr] !!! chunk {name}{'/' + shard if shard else ''} FAILED (exit {rc})",
                  flush=True)
            failed.append(csv.name)

    missing = [csv.name for _, _, csv in jobs if not csv.exists()]
    if failed or missing:
        print(f"[dr] INCOMPLETE — failed: {failed or '-'} missing: {missing or '-'}")
        return 1
    return _combine_chunks(families, args)


def _combine_chunks(families: list[str], args) -> int:
    files = sorted(args.chunks_dir.glob("chunk_*.csv"))
    # stale-shard guard: mixed shard families for one base name double count.
    fam: dict[str, set[str]] = {}
    for c in files:
        m = re.match(r"chunk_(.+)_s\d+of(\d+)$", c.stem)
        if m:
            fam.setdefault(m.group(1), set()).add(m.group(2))
    mixed = {b: ns for b, ns in fam.items() if len(ns) > 1}
    if mixed:
        raise SystemExit(f"[dr] mixed shard families would double count: {mixed}")

    df = pd.concat([pd.read_csv(c) for c in files], ignore_index=True)
    df = (df.groupby(["month", "blockchain", "token", "ref_code", "source"])["dr_usd"]
          .sum().reset_index())
    per_source = {key: sub.drop(columns=["source"])
                  for key, sub in df.groupby("source") if key in families}
    return _write(per_source, args.out)


def _run_monolithic(families: list[str], args) -> int:
    per_source = {}
    for key in families:
        print(f"[dr] computing monthly DR for {key} ...", flush=True)
        per_source[key] = pipeline.source_monthly(key, args.end, build_source_legs)
        print(f"[dr]   {len(per_source[key])} monthly rows", flush=True)
    return _write(per_source, args.out)


def _write(per_source: dict, out_dir: Path) -> int:
    out = pipeline.combine(per_source)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, df in out.items():
        p = out_dir / f"{name}.csv"
        df.to_csv(p, index=False)
        print(f"[dr] wrote {p} ({len(df)} rows)", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=",".join(pipeline.SOURCE_MONTHLY),
                    help="comma-separated subset of: " + ",".join(pipeline.SOURCE_MONTHLY))
    ap.add_argument("--end", type=_d, default=date(2026, 7, 1))
    ap.add_argument("--out", type=Path, default=ROOT / "hypersync-results" / "dr")
    ap.add_argument("--chunks-dir", type=Path,
                    default=ROOT / "hypersync-results" / "dr_full")
    ap.add_argument("--fresh", action="store_true",
                    help="recompute chunks even if their checkpoint CSV exists")
    ap.add_argument("--list", action="store_true", help="print the chunk plan and exit")
    ap.add_argument("--monolithic", action="store_true",
                    help="legacy in-process path (OOMs the 3.7GB box on a full run)")
    args = ap.parse_args()

    families = [k.strip() for k in args.sources.split(",")]
    unknown = [k for k in families if k not in pipeline.SOURCE_MONTHLY]
    if unknown:
        ap.error(f"unknown sources: {unknown}")
    if args.monolithic:
        return _run_monolithic(families, args)
    return _run_chunked(families, args)


if __name__ == "__main__":
    raise SystemExit(main())
