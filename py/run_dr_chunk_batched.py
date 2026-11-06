"""Low-memory, resumable worker for an existing sharded DR chunk.

This is deliberately a separate worker from :mod:`run_dr_chunk`: changing the
canonical worker would change its checkpoint fingerprint and invalidate good
outer-shard checkpoints.  The canonical shard identity remains ``k/N``.  This
worker only splits that shard's per-user TWA calculation into sequential,
temporary batches, then publishes the normal ``chunk_*_s{k}of{N}.parquet`` and
month-end state checkpoint.

The preparation stage writes the already-filtered legs to Parquet.  A killed
TWA batch can therefore resume without decoding the cached HyperSync history
again.  Batch metadata is committed last, so partial writes are never reused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))

from drhs import state_checkpoint, twa  # noqa: E402
from drhs.window import DEFAULT_END, beyond_cutoff_message  # noqa: E402
from run_dr_chunk import (  # noqa: E402
    DEFAULT_CHUNKS_DIR,
    DEFAULT_STATE_DIR,
    _monthly,
    build_target_legs,
    check_archive_coverage,
    checkpoint_fingerprint,
    chunk_parquet,
    chunk_plan,
    ensure_manifest,
    parse_shard,
    scan_chains,
)
from run_source import SPECS  # noqa: E402

FORMAT = "dr-twa-subbatches-v2"


def sub_batch(user_addr: str, batches: int) -> int:
    """Balanced deterministic partition independent of address bit patterns."""
    digest = hashlib.sha256(user_addr.lower().encode()).digest()
    return int.from_bytes(digest[:8], "big") % batches


def worker_fingerprint() -> str:
    """Bind temporary sub-batches to this worker without invalidating the
    canonical month-end checkpoint when only the batching implementation
    changes."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def validate_cutoff(end: date) -> None:
    """This worker publishes complete month-end state, so only month
    boundaries supported by the checkpoint system are valid cutoffs."""
    if end.day != 1 or end < state_checkpoint.FIRST_CUTOFF:
        raise SystemExit(
            "batched worker requires a supported first-of-month --end "
            f"on or after {state_checkpoint.FIRST_CUTOFF} (got {end})"
        )


def _atomic_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _identity(name: str, shard: str, end: date, batches: int) -> dict:
    return {
        "format": FORMAT,
        "chunk": name,
        "shard": shard,
        "end": end.isoformat(),
        "batches": batches,
        "fingerprint": checkpoint_fingerprint(name, shard),
        # The canonical fingerprint intentionally excludes this optional
        # low-memory worker. Its temporary files must still be invalidated if
        # the batching/finalization implementation changes.
        "worker_fingerprint": worker_fingerprint(),
    }


def _job_dir(root: Path, name: str, shard: str, end: date, batches: int) -> Path:
    return root / end.isoformat() / (
        f"{name}_s{shard.replace('/', 'of')}_b{batches}"
    )


def _batch_paths(job_dir: Path, batch: int) -> tuple[Path, Path, Path, Path, Path]:
    stem = job_dir / f"batch_{batch}"
    return (
        stem.with_suffix(".legs.parquet"),
        stem.with_suffix(".opening.parquet"),
        stem.with_suffix(".monthly.parquet"),
        stem.with_suffix(".state.parquet"),
        stem.with_suffix(".json"),
    )


def _prepared(job_dir: Path, identity: dict) -> dict | None:
    meta = _read_json(job_dir / "prepared.json")
    if meta is None or any(meta.get(k) != v for k, v in identity.items()):
        return None
    batches = identity["batches"]
    for b in range(batches):
        legs, opening, *_ = _batch_paths(job_dir, b)
        if not legs.exists() or not opening.exists():
            return None
    if meta.get("has_prior_monthly") and not (job_dir / "prior_monthly.parquet").exists():
        return None
    return meta


def prepare(name: str, shard: str, end: date, batches: int, job_dir: Path,
            state_dir: Path) -> int:
    identity = _identity(name, shard, end, batches)
    if _prepared(job_dir, identity) is not None:
        print(f"[batched] prepared legs exist: {job_dir}", flush=True)
        return 0

    family, src, target, plan_n = chunk_plan()[name]
    k, n = parse_shard(shard, plan_n)
    check_archive_coverage(scan_chains([name]), end)

    opening = prior_monthly = None
    scan_start = None
    fingerprint = identity["fingerprint"]
    if SPECS[src].incremental and end.day == 1 and end > state_checkpoint.FIRST_CUTOFF:
        prior_cutoff = state_checkpoint.previous_month_cutoff(end)
        loaded = state_checkpoint.load_snapshot(
            state_dir, prior_cutoff, name, shard, fingerprint)
        if loaded is not None:
            opening, prior_monthly = loaded
            scan_start = prior_cutoff
            print(f"[batched] resuming state at {scan_start}", flush=True)
        else:
            print(f"[batched] no compatible state at {prior_cutoff}; full replay",
                  flush=True)

    try:
        new_legs = build_target_legs(src, target, end, scan_start)
    except state_checkpoint.FullReplayRequired as exc:
        print(f"[batched] {exc}; full replay", flush=True)
        opening = prior_monthly = scan_start = None
        new_legs = build_target_legs(src, target, end)

    new_legs = new_legs[
        new_legs["user_addr"].map(lambda u: int(u[2:10], 16) % n == k)
    ].copy()
    if opening is None:
        opening = pd.DataFrame(columns=state_checkpoint.STATE_COLUMNS)

    job_dir.mkdir(parents=True, exist_ok=True)
    counts: list[dict] = []
    for b in range(batches):
        legs_path, opening_path, *_ = _batch_paths(job_dir, b)
        batch_legs = new_legs[
            new_legs["user_addr"].map(lambda u: sub_batch(u, batches) == b)
        ].copy()
        batch_opening = opening[
            opening["user_addr"].map(lambda u: sub_batch(u, batches) == b)
        ].copy()
        state_checkpoint.atomic_parquet(batch_legs, legs_path)
        state_checkpoint.atomic_parquet(batch_opening, opening_path)
        counts.append({"batch": b, "legs": len(batch_legs),
                       "opening": len(batch_opening)})
        print(f"[batched] prepared {b}/{batches}: {len(batch_legs)} legs, "
              f"{len(batch_opening)} opening rows", flush=True)

    has_prior = prior_monthly is not None
    if has_prior:
        state_checkpoint.atomic_parquet(prior_monthly, job_dir / "prior_monthly.parquet")
    meta = dict(identity, mode="incremental" if scan_start else "full",
                scan_start=scan_start.isoformat() if scan_start else None,
                has_prior_monthly=has_prior, counts=counts)
    _atomic_json(job_dir / "prepared.json", meta)
    return 0


def _batch_complete(job_dir: Path, identity: dict, batch: int) -> bool:
    *_, monthly_path, state_path, meta_path = _batch_paths(job_dir, batch)
    meta = _read_json(meta_path)
    return bool(
        meta is not None
        and all(meta.get(k) == v for k, v in identity.items())
        and meta.get("batch") == batch
        and monthly_path.exists()
        and state_path.exists()
    )


def compute_batch(name: str, shard: str, end: date, batches: int,
                  job_dir: Path, batch: int) -> int:
    identity = _identity(name, shard, end, batches)
    prepared = _prepared(job_dir, identity)
    if prepared is None:
        raise SystemExit("prepared legs are missing or incompatible")
    if _batch_complete(job_dir, identity, batch):
        print(f"[batched] batch {batch}/{batches} exists, skipping", flush=True)
        return 0

    family, _src, _target, _plan_n = chunk_plan()[name]
    _srcs, reclass, conv_builder, is_sp = __import__(
        "drhs.revenue.pipeline", fromlist=["SOURCE_MONTHLY"]
    ).SOURCE_MONTHLY[family]
    if is_sp:
        raise SystemExit("batched worker refuses SP sources")

    legs_path, opening_path, monthly_path, state_path, meta_path = _batch_paths(
        job_dir, batch)
    new_legs = pd.read_parquet(legs_path)
    opening = pd.read_parquet(opening_path)
    scan_start = (date.fromisoformat(prepared["scan_start"])
                  if prepared.get("scan_start") else None)
    opening_arg = opening if prepared["mode"] == "incremental" else None
    legs = (state_checkpoint.incremental_legs(opening_arg, new_legs, scan_start)
            if opening_arg is not None else new_legs)
    fill = min(end, DEFAULT_END) - timedelta(days=1)
    print(f"[batched] batch {batch}/{batches}: {len(new_legs)} new legs; TWA ...",
          flush=True)
    tw = twa.compute_twa(legs, fill_through=fill)
    del legs
    current = _monthly(tw, family, reclass, conv_builder, is_sp, fill)
    del tw
    closing = state_checkpoint.closing_state(new_legs, opening_arg)
    state_checkpoint.atomic_parquet(current, monthly_path)
    state_checkpoint.atomic_parquet(closing, state_path)
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    _atomic_json(meta_path, dict(identity, batch=batch,
                                monthly_rows=len(current), state_rows=len(closing),
                                peak_rss_mb=peak_mb))
    print(f"[batched] batch {batch}/{batches} complete; peak RSS {peak_mb}MB",
          flush=True)
    return 0


def combine_monthly(frames: list[pd.DataFrame], prior: pd.DataFrame | None) -> pd.DataFrame:
    parts = list(frames)
    if prior is not None:
        parts.append(prior)
    combined = pd.concat(parts, ignore_index=True)
    value_columns = [c for c in combined.columns if c != "dr_usd"]
    return combined.groupby(value_columns, as_index=False, dropna=False)["dr_usd"].sum()


def finalize(name: str, shard: str, end: date, batches: int, job_dir: Path,
             chunks_dir: Path, state_dir: Path) -> int:
    identity = _identity(name, shard, end, batches)
    prepared = _prepared(job_dir, identity)
    if prepared is None:
        raise SystemExit("prepared legs are missing or incompatible")
    missing = [b for b in range(batches)
               if not _batch_complete(job_dir, identity, b)]
    if missing:
        raise SystemExit(f"incomplete TWA batches: {missing}")

    monthly_frames = []
    state_frames = []
    for b in range(batches):
        *_, monthly_path, state_path, _meta_path = _batch_paths(job_dir, b)
        monthly_frames.append(pd.read_parquet(monthly_path))
        state_frames.append(pd.read_parquet(state_path))
    prior = (pd.read_parquet(job_dir / "prior_monthly.parquet")
             if prepared.get("has_prior_monthly") else None)
    result = combine_monthly(monthly_frames, prior)
    closing = pd.concat(state_frames, ignore_index=True)

    out = chunk_parquet(chunks_dir, name, shard)
    state_checkpoint.atomic_parquet(result, out)
    state_checkpoint.write_snapshot(
        state_dir, end, name, shard, closing, result, identity["fingerprint"])
    print(f"[batched] published {out} and canonical {end} state", flush=True)
    return 0


def _run_stage(args, stage: str, batch: int | None = None) -> int:
    cmd = [sys.executable, str(Path(__file__)), args.chunk,
           "--shard", args.shard, "--end", args.end.isoformat(),
           "--batches", str(args.batches), "--stage", stage,
           "--chunks-dir", str(args.chunks_dir),
           "--state-dir", str(args.state_dir),
           "--subchunks-dir", str(args.subchunks_dir)]
    if batch is not None:
        cmd += ["--batch", str(batch)]
    rc = subprocess.call(cmd)
    return 128 - rc if rc < 0 else rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chunk")
    ap.add_argument("--shard", required=True)
    ap.add_argument("--end", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                    default=DEFAULT_END)
    ap.add_argument("--batches", type=int, default=4)
    ap.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS_DIR)
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    ap.add_argument("--subchunks-dir", type=Path,
                    default=REPO / "hypersync-results" / "dr_subchunks")
    ap.add_argument("--stage", choices=("run", "prepare", "compute", "finalize"),
                    default="run")
    ap.add_argument("--batch", type=int)
    args = ap.parse_args()
    if args.batches < 2:
        raise SystemExit("--batches must be >= 2")
    if args.end > DEFAULT_END:
        raise SystemExit(beyond_cutoff_message(args.end))
    validate_cutoff(args.end)
    if args.chunk not in chunk_plan():
        raise SystemExit(f"unknown chunk {args.chunk}")
    _family, _src, _target, plan_n = chunk_plan()[args.chunk]
    if plan_n is None:
        raise SystemExit("batched worker requires an already-sharded chunk")
    parse_shard(args.shard, plan_n)
    ensure_manifest(args.chunks_dir, args.end)
    identity = _identity(args.chunk, args.shard, args.end, args.batches)
    job_dir = _job_dir(args.subchunks_dir, args.chunk, args.shard,
                       args.end, args.batches)

    out = chunk_parquet(args.chunks_dir, args.chunk, args.shard)
    if (args.stage == "run" and out.exists()
            and state_checkpoint.snapshot_complete(
                args.state_dir, args.end, args.chunk, args.shard,
                identity["fingerprint"])):
        print(f"[batched] canonical checkpoint exists, skipping {args.shard}")
        return 0

    if args.stage == "prepare":
        return prepare(args.chunk, args.shard, args.end, args.batches,
                       job_dir, args.state_dir)
    if args.stage == "compute":
        if args.batch is None or not 0 <= args.batch < args.batches:
            raise SystemExit("--batch must select one configured batch")
        return compute_batch(args.chunk, args.shard, args.end, args.batches,
                             job_dir, args.batch)
    if args.stage == "finalize":
        return finalize(args.chunk, args.shard, args.end, args.batches,
                        job_dir, args.chunks_dir, args.state_dir)

    rc = _run_stage(args, "prepare")
    if rc:
        return rc
    for b in range(args.batches):
        rc = _run_stage(args, "compute", b)
        if rc:
            return rc
    return _run_stage(args, "finalize")


if __name__ == "__main__":
    raise SystemExit(main())
