"""Run the full DR pipeline off HyperSync (no Dune) and write the rollups.

    .venv/bin/python py/run_dr_pipeline.py [--sources stusds,farms] [--end 2026-08-01]

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
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")

from drhs.revenue import pipeline  # noqa: E402
from drhs.sources.template_ab import DEFAULT_END  # noqa: E402
from run_dr_chunk import chunk_csv, chunk_plan, ensure_manifest, load_chunks  # noqa: E402
from run_source import build_source_legs  # noqa: E402


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _jobs(families: list[str] | None, chunks_dir: Path):
    """(name, shard, checkpoint path) for every planned job."""
    out = []
    for name, (_f, _s, _t, n) in chunk_plan(families).items():
        for shard in ([None] if not n else [f"{k}/{n}" for k in range(n)]):
            out.append((name, shard, chunk_csv(chunks_dir, name, shard)))
    return out


def _run_chunked(families: list[str], args) -> int:
    jobs = _jobs(families, args.chunks_dir)

    if args.list:
        for name, shard, csv in jobs:
            state = "done" if csv.exists() else "pending"
            print(f"{csv.name:55s} {state}")
        return 0

    if args.fresh:
        # wipe EVERYTHING (incl. out-of-plan strays and the manifest): a fresh
        # run must not inherit any file this plan does not account for.
        for f in args.chunks_dir.glob("chunk_*.csv"):
            f.unlink()
        mf = args.chunks_dir / "manifest.json"
        if mf.exists():
            mf.unlink()
    ensure_manifest(args.chunks_dir, args.end)

    failed: list[str] = []
    for name, shard, csv in jobs:
        if csv.exists():
            print(f"[dr] {csv.name} exists, skipping", flush=True)
            continue
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
    # strict: only files of the FULL plan may exist in the dir (a stray —
    # legacy naming, an unsharded checkpoint beside its shard set — would
    # silently double count; load_chunks errors on it). Selected families
    # must be complete; other families may be partially present and are
    # read but filtered out below.
    plan_files = [csv for _, _, csv in _jobs(None, args.chunks_dir)]
    missing = [csv.name for _, _, csv in _jobs(families, args.chunks_dir)
               if not csv.exists()]
    if missing:
        raise SystemExit(f"[dr] missing planned checkpoints: {missing}")
    df = load_chunks(args.chunks_dir, expected=[p for p in plan_files if p.exists()])
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
    ap.add_argument("--end", type=_d, default=date(2026, 8, 1))
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
    if args.end > DEFAULT_END:
        ap.error(f"--end {args.end} is beyond the deployed scan cutoff {DEFAULT_END}; "
                 "extend the settlement window first (DEFAULT_END in "
                 "drhs/sources/template_ab.py + the fill caps)")
    if args.monolithic:
        return _run_monolithic(families, args)
    return _run_chunked(families, args)


if __name__ == "__main__":
    raise SystemExit(main())
