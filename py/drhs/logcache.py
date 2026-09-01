"""Persistent on-disk cache of HyperSync log queries (parquet, append-only).

Event logs are immutable once final, so a query's result for a historical
block range never changes — yet every settlement re-downloaded all of it
(the dominant wall-clock cost: ~80% of a chunk, and the 8 Base shards each
re-fetched the identical event set). This layer persists fetched logs per
(chain, query shape) and serves later runs from disk, fetching only the
blocks it has not seen.

Layout (under ``$DRHS_CACHE_DIR``, default ``~/.cache/drhs``)::

    logs/{chain}/{key}/meta.json                 # query spec + coverage
    logs/{chain}/{key}/seg_{from}_{to}.parquet   # rows, contiguous ranges

``key`` hashes the chain + canonicalized selections + log fields: ANY change
to what is asked for is a different cache entry, never a partial reuse.

Reorg safety: only blocks at least ``SAFE_DEPTH_BLOCKS`` (~1h of wall time,
per chain) below the archive head observed DURING the fetch are persisted;
the young tail is always re-fetched live. Coverage is one contiguous
``[cached_from, cached_through]`` window — segments must abut exactly, and
overlap or a gap is a hard error (never a silent double count / hole).

Bypass: env ``DRHS_NO_LOG_CACHE=1`` (or the pipeline's ``--no-cache``) makes
every query hit the network, byte-for-byte the pre-cache behaviour. The
cache is trusted by default; ``py/verify_log_cache.py`` re-fetches sampled
cached ranges live and diffs them for on-demand assurance.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ~1 hour of blocks per chain — deeper than any credible reorg on these
# chains, shallow enough that a monthly settlement caches essentially all of
# its window. Unknown chains get the most conservative depth.
SAFE_DEPTH_BLOCKS: dict[str, int] = {
    "ethereum": 300,        # 12s blocks
    "base": 1800,           # 2s
    "optimism": 1800,       # 2s
    "arbitrum": 14400,      # 0.25s
    "unichain": 3600,       # 1s
    "avalanche_c": 1800,    # ~2s
}
_DEFAULT_SAFE_DEPTH = 14400

_COLUMNS = ["block_number", "log_index", "block_time", "address",
            "topic0", "topic1", "topic2", "topic3", "data", "transaction_hash"]


def enabled() -> bool:
    return not os.environ.get("DRHS_NO_LOG_CACHE")


def cache_key(chain: str, selections: list[dict[str, Any]],
              log_fields: list[str]) -> str:
    """Content hash of exactly what is asked for. Canonical JSON (sorted keys,
    lowercased strings) so semantically identical queries share an entry and
    ANY difference — one more address, one more topic — is a separate one."""
    def _canon(v):
        if isinstance(v, dict):
            return {k: _canon(v[k]) for k in sorted(v)}
        if isinstance(v, list):
            return [_canon(x) for x in v]
        return v.lower() if isinstance(v, str) else v
    blob = json.dumps({"chain": chain, "selections": _canon(selections),
                       "log_fields": list(log_fields)}, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def entry_dir(chain: str, key: str) -> Path:
    from drhs.hypersync import _cache_dir
    return _cache_dir() / "logs" / chain / key


@dataclass
class Meta:
    chain: str
    selections: list
    log_fields: list
    cached_from: int
    cached_through: int
    segments: list  # [{"file", "from", "to"}], contiguous & ascending


def load_meta(d: Path) -> Meta | None:
    p = d / "meta.json"
    if not p.exists():
        return None
    try:
        m = Meta(**json.loads(p.read_text()))
    except Exception:  # noqa: BLE001 — corrupt meta: treat as no cache
        return None
    lo = m.cached_from
    for seg in m.segments:
        if seg["from"] != lo or not (d / seg["file"]).exists():
            return None  # gap, overlap, or missing file — refuse, refetch
        lo = seg["to"] + 1
    if lo != m.cached_through + 1:
        return None
    return m


def _write_meta(d: Path, m: Meta) -> None:
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(m.__dict__))
    os.replace(tmp, d / "meta.json")


def read_rows(d: Path, m: Meta, from_block: int, to_block: int) -> list:
    """Cached LogRows in ``[from_block, to_block]`` (must lie inside coverage),
    in (block, fetch) order — parquet preserves row order, so this replays the
    original fetch order exactly."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from drhs.hypersync import LogRow
    rows: list = []
    for seg in m.segments:
        if seg["to"] < from_block or seg["from"] > to_block:
            continue
        t = pq.read_table(d / seg["file"], columns=_COLUMNS)
        if seg["from"] < from_block or seg["to"] > to_block:
            t = t.filter((pc.field("block_number") >= from_block)
                         & (pc.field("block_number") <= to_block))
        cols = [t.column(c).to_pylist() for c in _COLUMNS]
        rows.extend(LogRow(*vals) for vals in zip(*cols))
    return rows


def append_segment(d: Path, m: Meta | None, meta_args: dict,
                   rows: list, seg_from: int, seg_to: int) -> Meta:
    """Persist ``rows`` (all with seg_from <= block <= seg_to) as one segment.
    The new range must extend coverage contiguously on either side; anything
    else is refused (a gap would silently hole the replay)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    d.mkdir(parents=True, exist_ok=True)
    fname = f"seg_{seg_from}_{seg_to}.parquet"
    table = pa.table({c: [getattr(r, c) for r in rows] for c in _COLUMNS})
    tmp = d / (fname + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, d / fname)

    seg = {"file": fname, "from": seg_from, "to": seg_to}
    if m is None:
        m = Meta(cached_from=seg_from, cached_through=seg_to,
                 segments=[seg], **meta_args)
    elif seg_from == m.cached_through + 1:
        m.segments.append(seg)
        m.cached_through = seg_to
    elif seg_to == m.cached_from - 1:
        m.segments.insert(0, seg)
        m.cached_from = seg_from
    else:
        raise RuntimeError(
            f"log cache {d}: segment [{seg_from},{seg_to}] does not abut "
            f"coverage [{m.cached_from},{m.cached_through}]")
    _write_meta(d, m)
    return m
