"""Worker: compute ONE target-chunk's monthly DR and write its Parquet checkpoint.

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
        [--end YYYY-MM-DD] [--chunks-dir hypersync-results/dr_full]
    .venv/bin/python py/run_dr_chunk.py --list
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from drhs import hypersync, state_checkpoint, twa  # noqa: E402
from drhs.revenue import conversion, deployment, monthly, pipeline  # noqa: E402
from drhs.window import (DEFAULT_END, REWARD_END, REWARD_START,  # noqa: E402
                         beyond_cutoff_message, midnight_ts)
from run_source import SPECS, build_source_legs  # noqa: E402

DEFAULT_CHUNKS_DIR = REPO / "hypersync-results" / "dr_full"
DEFAULT_STATE_DIR = REPO / "hypersync-results" / "dr_state"
CHECKPOINT_FORMAT = "parquet-v1"
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


def checkpoint_fingerprint(name: str, shard: str | None) -> str:
    """Bind reusable state to attribution code, configuration and job shape.

    The deployed window and HyperSync/cache transports are deliberately
    excluded: advancing the cutoff is the reason the state is reusable, while
    transport changes cannot alter legs. Every attribution source/revenue
    module plus the registry and worker are included, so a methodology or
    target/config edit safely falls back to full replay.
    """
    family, src, target, plan_n = chunk_plan()[name]
    identity = {
        "name": name, "shard": shard, "family": family, "source": src,
        "planned_shards": plan_n, "target": repr(target),
        # DEFAULT_END is intentionally absent, but the authorized reward
        # window and exact UTC boundary conversion are methodology.
        "reward_start": REWARD_START.isoformat(),
        "reward_end": REWARD_END.isoformat(),
        "midnight_ts_source": inspect.getsource(midnight_ts),
    }
    h = hashlib.sha256(json.dumps(identity, sort_keys=True).encode())
    paths = [REPO / "py" / "run_dr_chunk.py", REPO / "py" / "run_source.py"]
    paths += sorted((REPO / "py" / "drhs" / "sources").glob("*.py"))
    paths += sorted((REPO / "py" / "drhs" / "revenue").glob("*.py"))
    paths += [REPO / "py" / "drhs" / "events.py",
              REPO / "py" / "drhs" / "twa.py",
              REPO / "py" / "drhs" / "state_checkpoint.py"]
    for path in sorted(set(paths)):
        h.update(str(path.relative_to(REPO)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def require_complete_state(state_dir: Path, cutoff: date,
                           jobs: list[tuple[str, str | None]]) -> None:
    """Refuse consumers that bypass the orchestrator when state is stale."""
    if cutoff.day != 1 or cutoff < state_checkpoint.FIRST_CUTOFF:
        return
    stale = [state_checkpoint.job_key(name, shard) for name, shard in jobs
             if not state_checkpoint.snapshot_complete(
                 state_dir, cutoff, name, shard,
                 checkpoint_fingerprint(name, shard))]
    if stale:
        sample = ", ".join(stale[:5])
        more = f" (+{len(stale) - 5} more)" if len(stale) > 5 else ""
        raise SystemExit(
            f"state checkpoints for end={cutoff} are missing or incompatible: "
            f"{sample}{more} — run run_dr_pipeline.py first")


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


def scan_chains(names) -> set[str]:
    """Every chain the given chunks will scan: their targets' chains PLUS the
    chains of the conversion/deployment series each family builds regardless
    of target (conversion.susds_rates/stusds_rates hardcode ethereum;
    sp_vault_rates adds avalanche_c). The coverage guard must include them —
    an L2-only run would otherwise pass the probe while the ethereum-based
    conversion series head-clamps and forward-fills a stale rate."""
    plan = chunk_plan()
    chains: set[str] = set()
    for name in names:
        family, src, t, _n = plan[name]
        chains |= {t.blockchain, "ethereum"}
        if pipeline.SOURCE_MONTHLY[family][3]:  # is_sp
            chains.add("avalanche_c")
        # anchored programs scan their ORIGIN chains too (Li.Fi bridges into the
        # target): a lagging origin archive would silently under-anchor the
        # month-end bridges, so those chains join the coverage guard.
        for p in SPECS[src].synthetic:
            for oc in getattr(p, "origin_chains", ()):
                if oc in hypersync.HYPERSYNC_HOSTS:
                    chains.add(oc)
    return chains


def check_archive_coverage(chains, end: date) -> None:
    """Refuse to scan a window the HyperSync archives have not fully indexed
    yet: a head behind ``end`` silently truncates the scan at a DIFFERENT
    effective cutoff as the head advances mid-run (observed on the Aug-2026
    settlement: the eth head was ~20h behind month-end at launch). Called by
    the orchestrator for the chains its pending chunks will scan, and by this
    worker for its own — so a documented standalone chunk rerun gets the same
    protection as an orchestrated launch."""
    end_ts = midnight_ts(end)
    behind, unreachable = [], []
    for chain in sorted(set(chains)):
        try:
            _blk, ts = hypersync.returnable_head(chain)
        except hypersync.HyperSyncError as e:
            unreachable.append(f"{chain} ({e})")
            continue
        if ts < end_ts:
            behind.append(
                f"{chain} (head {datetime.fromtimestamp(ts, tz=timezone.utc):%Y-%m-%d %H:%M}Z)")
    if behind or unreachable:
        parts = []
        if behind:
            parts.append(f"archives not caught up to end={end}: {', '.join(behind)} — "
                         "wait for the archive heads to pass the window end")
        if unreachable:
            parts.append(f"archive unreachable/inconsistent (coverage of end={end} "
                         f"cannot be verified): {', '.join(unreachable)} — retry "
                         "when HyperSync responds")
        raise SystemExit("; ".join(parts))


def chunk_parquet(chunks_dir: Path, name: str, shard: str | None) -> Path:
    suffix = f"_s{shard.replace('/', 'of')}" if shard else ""
    return chunks_dir / f"chunk_{name}{suffix}.parquet"


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
        manifest = json.loads(mf.read_text())
        have = manifest.get("end")
        have_format = manifest.get("format")
        if have != end.isoformat() or have_format != CHECKPOINT_FORMAT:
            raise SystemExit(
                f"{chunks_dir} holds checkpoints for end={have}, format={have_format}, "
                f"requested end={end.isoformat()}, format={CHECKPOINT_FORMAT} — "
                "rerun with --fresh or a different --chunks-dir")
        return
    chunks_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(mf, json.dumps({
        "end": end.isoformat(),
        "format": CHECKPOINT_FORMAT,
    }))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def load_chunks(chunks_dir: Path, expected: list[Path] | None = None):
    """Read Parquet checkpoints and sum to (month, blockchain, token, ref_code,
    source) — the ONE combine used by the pipeline and the workbook builder.

    With ``expected`` (the orchestrator's plan): every expected file must
    exist and NO other chunk_*.parquet may be present — a stray file (legacy
    naming, an unsharded checkpoint next to its shard set) would silently
    double count, so it is a hard error. Without ``expected`` (workbook
    builder on an already-validated dir): all files are read, guarded against
    mixed shard-N families.
    """
    import pandas as pd
    files = sorted(chunks_dir.glob("chunk_*.parquet"))
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
    if not files:
        raise SystemExit(f"chunks dir {chunks_dir} contains no Parquet checkpoints")
    df = pd.concat([pd.read_parquet(c) for c in files], ignore_index=True)
    return (df.groupby(["month", "blockchain", "token", "ref_code", "source"])["dr_usd"]
            .sum().reset_index())


def build_target_legs(src: str, t, end: date, scan_start: date | None = None):
    """Legs for ONE target — build_source_legs with a target override, so the
    SourceSpec wiring (exclusions, synthetic programs, re-routes) has exactly
    one home."""
    return build_source_legs(src, end, targets=[t], scan_start=scan_start)


def _monthly(tw, family: str, reclass, conv_builder, is_sp: bool, fill: date):
    if is_sp:
        dep = deployment.deployment_ratios(tw, end=fill)
        dep_map = {(r.blockchain, r.vault_symbol, r.dt): r.deployment_ratio
                   for r in dep.itertuples()}
        result = monthly.monthly_dr(
            tw, reclassify=reclass,
            conv_lookup=monthly.sp_conv(conversion.sp_vault_rates()),
            sp_deployment=dep_map)
    else:
        result = monthly.monthly_dr(tw, reclassify=reclass, conv_lookup=conv_builder())
    result["source"] = family
    return result


def compute_chunk(name: str, shard: str | None, end: date, *,
                  state_dir: Path | None = None, full_replay: bool = False):
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

    opening = prior_monthly = None
    scan_start = None
    fingerprint = checkpoint_fingerprint(name, shard)
    if (state_dir is not None and not full_replay and SPECS[src].incremental
            and end.day == 1 and end > state_checkpoint.FIRST_CUTOFF):
        prior_cutoff = state_checkpoint.previous_month_cutoff(end)
        loaded = state_checkpoint.load_snapshot(
            state_dir, prior_cutoff, name, shard, fingerprint)
        if loaded is not None:
            opening, prior_monthly = loaded
            scan_start = prior_cutoff
            print(f"[{name}] resuming from complete state at {scan_start}", flush=True)
        else:
            print(f"[{name}] no complete state at {prior_cutoff}; full replay", flush=True)
    elif state_dir is not None and not SPECS[src].incremental:
        print(f"[{name}] protocol-state source; full replay", flush=True)

    try:
        new_legs = build_target_legs(src, t, end, scan_start)
    except state_checkpoint.FullReplayRequired as exc:
        print(f"[{name}] {exc}; full replay", flush=True)
        opening = prior_monthly = scan_start = None
        new_legs = build_target_legs(src, t, end)
    if shard is not None:
        k, n = parse_shard(shard, plan_n)
        new_legs = new_legs[
            new_legs["user_addr"].map(lambda u: int(u[2:10], 16) % n == k)].copy()
    legs = (state_checkpoint.incremental_legs(opening, new_legs, scan_start)
            if opening is not None else new_legs)
    print(f"[{name}{'/' + shard if shard else ''}] {len(new_legs)} new legs; TWA ...",
          flush=True)
    tw = twa.compute_twa(legs, fill_through=fill)
    del legs
    print(f"[{name}] {len(tw)} TWA rows; monthly ...", flush=True)
    current = _monthly(tw, family, reclass, conv_builder, is_sp, fill)
    del tw
    closing = state_checkpoint.closing_state(new_legs, opening)
    del new_legs
    if prior_monthly is not None:
        m = pd.concat([prior_monthly, current], ignore_index=True)
        value_columns = [c for c in m.columns if c != "dr_usd"]
        m = m.groupby(value_columns, as_index=False, dropna=False)["dr_usd"].sum()
    else:
        m = current
    return m, closing, "incremental" if opening is not None else "full"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("chunk", nargs="?", help="chunk name (see --list)")
    ap.add_argument("--shard", default=None, help="k/N user-hash shard")
    ap.add_argument("--end", type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
                    default=DEFAULT_END)
    ap.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS_DIR)
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR,
                    help="month-end holder state (August 2026 onward)")
    ap.add_argument("--full-replay", action="store_true",
                    help="ignore prior state, but refresh the ending snapshot")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-cache", action="store_true",
                    help="bypass the persistent log cache (see drhs/logcache.py)")
    args = ap.parse_args()
    if args.no_cache:
        os.environ["DRHS_NO_LOG_CACHE"] = "1"

    if args.list or not args.chunk:
        for name, (family, _s, _t, n) in chunk_plan().items():
            print(f"{name:40s} family={family}" + (f"  shards={n}" if n else ""))
        return 0

    if args.end > DEFAULT_END:
        raise SystemExit(beyond_cutoff_message(args.end))
    ensure_manifest(args.chunks_dir, args.end)
    out = chunk_parquet(args.chunks_dir, args.chunk, args.shard)
    state_enabled = args.end.day == 1 and args.end >= state_checkpoint.FIRST_CUTOFF
    fingerprint = checkpoint_fingerprint(args.chunk, args.shard)
    if (not args.full_replay and out.exists()
            and (not state_enabled or state_checkpoint.snapshot_complete(
                args.state_dir, args.end, args.chunk, args.shard, fingerprint))):
        print(f"[{args.chunk}] {out.name} exists, skipping")
        return 0
    check_archive_coverage(scan_chains([args.chunk]), args.end)
    m, closing, mode = compute_chunk(
        args.chunk, args.shard, args.end,
        state_dir=args.state_dir if state_enabled else None,
        full_replay=args.full_replay)
    # Atomic checkpoint: a kill mid-write must never leave a truncated file
    # that a resume would accept as complete.
    tmp = out.with_suffix(".parquet.tmp")
    m.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, out)
    if state_enabled:
        state_checkpoint.write_snapshot(
            args.state_dir, args.end, args.chunk, args.shard, closing, m,
            fingerprint)
    import resource
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(f"[{args.chunk}] wrote {out} and {args.end} state "
          f"({mode}; {len(m)} rows; peak RSS {peak_mb}MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
