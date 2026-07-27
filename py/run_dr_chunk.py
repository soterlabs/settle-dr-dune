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
        [--end 2026-07-01] [--chunks-dir hypersync-results/dr_full]
    .venv/bin/python py/run_dr_chunk.py --list
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "py"))
from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from drhs import twa  # noqa: E402
from drhs.revenue import conversion, deployment, monthly, pipeline  # noqa: E402
from run_source import SPECS  # noqa: E402

DEFAULT_END = date(2026, 7, 1)
DEFAULT_CHUNKS_DIR = REPO / "hypersync-results" / "dr_full"

# Targets too large for one process even with compact legs: (source,
# blockchain, symbol) -> shard count. Tuned from the PRD validation run.
SHARDS: dict[tuple[str, str, str], int] = {
    ("susds_psm3", "base", "sUSDS"): 4,
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


def chunk_csv(chunks_dir: Path, name: str, shard: str | None) -> Path:
    suffix = f"_s{shard.replace('/', 'of')}" if shard else ""
    return chunks_dir / f"chunk_{name}{suffix}.csv"


def build_target_legs(src: str, t, end: date):
    """Legs for ONE target, honoring the source's exclusions + synthetic
    programs (equivalent to build_source_legs restricted to this target)."""
    s = SPECS[src]
    kw = {}
    if s.synthetic:
        kw["synthetic"] = s.synthetic
    if s.reroute:
        kw["reroute"] = s.reroute
    return s.template.build_legs([t], end_date=end, excluded=s.excluded, **kw)


def compute_chunk(name: str, shard: str | None, end: date):
    family, src, t, _n = chunk_plan()[name]
    _srcs, reclass, conv_builder, is_sp = pipeline.SOURCE_MONTHLY[family]
    fill = min(end, date(2026, 6, 30))

    legs = build_target_legs(src, t, end)
    if shard is not None:
        k, n = (int(x) for x in shard.split("/"))
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

    out = chunk_csv(args.chunks_dir, args.chunk, args.shard)
    if out.exists():
        print(f"[{args.chunk}] {out.name} exists, skipping")
        return 0
    m = compute_chunk(args.chunk, args.shard, args.end)
    args.chunks_dir.mkdir(parents=True, exist_ok=True)
    m.to_csv(out, index=False)
    import resource
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(f"[{args.chunk}] wrote {out} ({len(m)} rows; peak RSS {peak_mb}MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
