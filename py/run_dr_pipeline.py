"""Run the full DR pipeline off HyperSync (no Dune) and write the rollups.

    .venv/bin/python py/run_dr_pipeline.py [--sources stusds,farms] [--end YYYY-MM-DD]

Writes to hypersync-results/dr/:
    dr_monthly_combined.csv
    dr_rollup_by_refcode.csv
    dr_rollup_by_refcode_token.csv

Default mode is CHUNKED (docs/prd-chunked-pipeline.md): one target per
subprocess (worker: run_dr_chunk.py), sequential, with Parquet checkpoints under
--chunks-dir — a killed run resumes by skipping completed chunks; oversized
targets are user-hash sharded (exact by per-user independence). The
monolithic in-process path (--monolithic) remains for small source subsets
and debugging, but OOMs the 3.7GB production box on a full run.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(ROOT / ".env")

from drhs import state_checkpoint  # noqa: E402
from drhs.revenue import pipeline  # noqa: E402
from drhs.window import DEFAULT_END, beyond_cutoff_message  # noqa: E402
from run_dr_chunk import (checkpoint_fingerprint, chunk_parquet, chunk_plan,  # noqa: E402
                          check_archive_coverage, ensure_manifest, load_chunks,
                          scan_chains)
from run_source import build_source_legs  # noqa: E402


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _jobs(families: list[str] | None, chunks_dir: Path):
    """(name, shard, checkpoint path) for every planned job."""
    out = []
    for name, (_f, _s, _t, n) in chunk_plan(families).items():
        for shard in ([None] if not n else [f"{k}/{n}" for k in range(n)]):
            out.append((name, shard, chunk_parquet(chunks_dir, name, shard)))
    return out


def _job_done(name: str, shard: str | None, checkpoint: Path, args) -> bool:
    if not checkpoint.exists():
        return False
    if args.end.day != 1 or args.end < state_checkpoint.FIRST_CUTOFF:
        return True
    return state_checkpoint.snapshot_complete(
        args.state_dir, args.end, name, shard,
        checkpoint_fingerprint(name, shard))


def _run_chunked(families: list[str], args) -> int:
    jobs = _jobs(families, args.chunks_dir)

    if args.list:
        for name, shard, checkpoint in jobs:
            state = "done" if _job_done(name, shard, checkpoint, args) else "pending"
            print(f"{checkpoint.name:55s} {state}")
        return 0

    if not args.fresh:
        # cheap local integrity first: a mismatched --end must be named before
        # any network probing (with --fresh the manifest is wiped below anyway).
        ensure_manifest(args.chunks_dir, args.end)
    # Probe only the chains the pending chunks will scan (targets + conversion
    # chains — see scan_chains), BEFORE the --fresh wipe: lagging archives must
    # refuse the run without destroying good checkpoints, and a combine-only
    # rerun over complete checkpoints stays fully offline (no pending -> no
    # chains -> no probe).
    pending = {name for name, shard, checkpoint in jobs
               if args.fresh or args.full_replay
               or not _job_done(name, shard, checkpoint, args)}
    check_archive_coverage(scan_chains(pending), args.end)
    if args.fresh:
        # wipe EVERYTHING (incl. out-of-plan strays and the manifest): a fresh
        # run must not inherit any file this plan does not account for.
        for pattern in ("chunk_*.csv", "chunk_*.parquet"):
            for f in args.chunks_dir.glob(pattern):
                f.unlink()
        mf = args.chunks_dir / "manifest.json"
        if mf.exists():
            mf.unlink()
        ensure_manifest(args.chunks_dir, args.end)

    failed: list[str] = []
    for name, shard, checkpoint in jobs:
        if not args.fresh and not args.full_replay and _job_done(
                name, shard, checkpoint, args):
            print(f"[dr] {checkpoint.name} exists, skipping", flush=True)
            continue
        cmd = [sys.executable, "-u", str(Path(__file__).parent / "run_dr_chunk.py"),
               name, "--end", args.end.isoformat(), "--chunks-dir", str(args.chunks_dir),
               "--state-dir", str(args.state_dir)]
        if shard:
            cmd += ["--shard", shard]
        if args.full_replay:
            cmd += ["--full-replay"]
        print(f"[dr] chunk {name}{'/' + shard if shard else ''} ...", flush=True)
        rc = subprocess.run(cmd).returncode
        if rc != 0:
            print(f"[dr] !!! chunk {name}{'/' + shard if shard else ''} FAILED (exit {rc})",
                  flush=True)
            failed.append(checkpoint.name)

    missing = [checkpoint.name for name, shard, checkpoint in jobs
               if not _job_done(name, shard, checkpoint, args)]
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
    plan_files = [checkpoint for _, _, checkpoint in _jobs(None, args.chunks_dir)]
    missing = [checkpoint.name for _, _, checkpoint in _jobs(families, args.chunks_dir)
               if not checkpoint.exists()]
    if missing:
        raise SystemExit(f"[dr] missing planned checkpoints: {missing}")
    df = load_chunks(args.chunks_dir, expected=[p for p in plan_files if p.exists()])
    per_source = {key: sub.drop(columns=["source"])
                  for key, sub in df.groupby("source") if key in families}
    return _write(per_source, args.out)


def _run_monolithic(families: list[str], args) -> int:
    check_archive_coverage(scan_chains(chunk_plan(families)), args.end)
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
    # Default derives from the settled window — a hardcoded literal here is
    # exactly how the Aug-2026 settlement launched against the July window.
    ap.add_argument("--end", type=_d, default=DEFAULT_END)
    ap.add_argument("--out", type=Path, default=ROOT / "hypersync-results" / "dr")
    ap.add_argument("--chunks-dir", type=Path,
                    default=ROOT / "hypersync-results" / "dr_full")
    ap.add_argument("--state-dir", type=Path,
                    default=ROOT / "hypersync-results" / "dr_state",
                    help="complete month-end state (August 2026 onward)")
    ap.add_argument("--fresh", action="store_true",
                    help="recompute chunks even if their Parquet checkpoint exists")
    ap.add_argument("--full-replay", action="store_true",
                    help="audit escape hatch: ignore prior state and refresh all chunks")
    ap.add_argument("--list", action="store_true", help="print the chunk plan and exit")
    ap.add_argument("--monolithic", action="store_true",
                    help="legacy in-process path (OOMs the 3.7GB box on a full run)")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the persistent log cache: every scan fetches "
                         "from the network (pre-cache behaviour; audit escape "
                         "hatch — see drhs/logcache.py)")
    args = ap.parse_args()
    if args.no_cache:
        os.environ["DRHS_NO_LOG_CACHE"] = "1"  # inherited by chunk subprocesses

    families = [k.strip() for k in args.sources.split(",")]
    unknown = [k for k in families if k not in pipeline.SOURCE_MONTHLY]
    if unknown:
        ap.error(f"unknown sources: {unknown}")
    if args.end > DEFAULT_END:
        ap.error(beyond_cutoff_message(args.end))
    if args.monolithic:
        return _run_monolithic(families, args)
    return _run_chunked(families, args)


if __name__ == "__main__":
    raise SystemExit(main())
