"""Low-level Envio HyperSync client — raw log queries over HTTP.

Adapted from settlement-cycle's ``src/settle/extract/hypersync.py`` and trimmed
to what a *batch* DR pipeline needs: since we pin to a fixed historical end
block there is no reorg window to guard, so the Postgres-backed reorg-safe
store is dropped in favour of a plain on-disk pickle cache (block timestamps
and block-at-or-before resolutions are immutable given chain+block, so caching
them is always safe).

HyperSync is a stateless query API: POST a selection (addresses + topic
filters + block range) and it streams back matching logs, paginating by a
server-side time budget via ``next_block``. Auth is a bearer ``ENVIO_API_TOKEN``
(free at https://app.envio.dev/api-tokens; 401 without one).
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

_LOG = logging.getLogger(__name__)
_DEFAULT_TIMEOUT = 60
_MAX_PAGES = 100_000  # runaway backstop

# chain name -> HyperSync host.
HYPERSYNC_HOSTS: dict[str, str] = {
    "ethereum": "eth.hypersync.xyz",
    "base": "base.hypersync.xyz",
    "arbitrum": "arbitrum.hypersync.xyz",
    "optimism": "optimism.hypersync.xyz",
    "unichain": "unichain.hypersync.xyz",
    "avalanche_c": "avalanche.hypersync.xyz",
    "plume": "plume.hypersync.xyz",
    "monad": "monad.hypersync.xyz",
}

_DEFAULT_LOG_FIELDS = [
    "block_number", "log_index", "address",
    "topic0", "topic1", "topic2", "topic3", "data", "transaction_hash",
]
_DEFAULT_BLOCK_FIELDS = ["number", "timestamp"]


class HyperSyncError(RuntimeError):
    """Raised on HyperSync transport / auth / query errors."""


@dataclass(frozen=True)
class LogRow:
    block_number: int
    log_index: int
    block_time: int          # unix seconds, UTC
    address: str
    topic0: str | None
    topic1: str | None
    topic2: str | None
    topic3: str | None
    data: str
    transaction_hash: str | None = None


@dataclass
class QueryResult:
    rows: list[LogRow] = field(default_factory=list)
    archive_height: int = 0  # HyperSync's indexed chain head


def endpoint(chain: str) -> str:
    """Resolve the HyperSync ``/query`` URL for ``chain``."""
    override = os.environ.get(f"HYPERSYNC_URL_{chain.upper()}")
    if override:
        return override
    host = HYPERSYNC_HOSTS.get(chain)
    if not host:
        raise HyperSyncError(f"No HyperSync host mapping for chain {chain!r}")
    return f"https://{host}/query"


def _token() -> str:
    tok = os.environ.get("ENVIO_API_TOKEN")
    if not tok:
        raise HyperSyncError(
            "Missing env var ENVIO_API_TOKEN (free token at "
            "https://app.envio.dev/api-tokens; HyperSync returns 401 without it)"
        )
    return tok


def to_int(v: Any) -> int:
    """HyperSync JSON returns numerics as hex strings ('0x..') or ints."""
    if isinstance(v, int):
        return v
    s = str(v)
    return int(s, 16) if s.startswith("0x") else int(s)


def _lower(v: Any) -> str | None:
    return v.lower() if isinstance(v, str) else v


# --------------------------------------------------------------------------
# On-disk cache (immutable block facts only) — no Postgres, no reorg store.
# --------------------------------------------------------------------------
def _cache_dir() -> Path:
    d = Path(os.environ.get("DRHS_CACHE_DIR", str(Path.home() / ".cache" / "drhs")))
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _cache_get(key: str) -> Any:
    p = _cache_dir() / f"{key}.pkl"
    if p.exists():
        try:
            return pickle.loads(p.read_bytes())
        except Exception:  # noqa: BLE001 — corrupt cache entry, just refetch
            return None
    return None


def _cache_put(key: str, value: Any) -> None:
    p = _cache_dir() / f"{key}.pkl"
    p.write_bytes(pickle.dumps(value))


def _key(*parts: Any) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:32]


# --------------------------------------------------------------------------
# Core queries
# --------------------------------------------------------------------------
def query_logs(
    chain: str,
    selections: list[dict[str, Any]],
    from_block: int,
    to_block: int,
    *,
    log_fields: list[str] | None = None,
    block_fields: list[str] | None = None,
    post: Callable[..., Any] = requests.post,
) -> QueryResult:
    """Fetch all logs matching ``selections`` in ``[from_block, to_block]`` (inclusive).

    ``selections`` is HyperSync's ``logs`` array — each entry is
    ``{"address": [...], "topics": [[topic0...], [topic1...], ...]}``; multiple
    entries are OR'd. Pages are followed via ``next_block`` until ``to_block``.
    """
    lf = log_fields or _DEFAULT_LOG_FIELDS
    bf = block_fields or _DEFAULT_BLOCK_FIELDS
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}
    base = {"logs": selections, "field_selection": {"log": lf, "block": bf}}
    result = QueryResult()
    cursor = from_block
    end_exclusive = to_block + 1  # HyperSync to_block is exclusive
    for _ in range(_MAX_PAGES):
        if cursor >= end_exclusive:
            break
        body = {**base, "from_block": cursor, "to_block": end_exclusive}
        page = _execute(chain, body, headers, post)
        result.archive_height = max(
            result.archive_height, to_int(page.get("archive_height", 0) or 0)
        )
        for group in page.get("data") or []:
            ts_by_block = {
                to_int(b["number"]): to_int(b["timestamp"])
                for b in (group.get("blocks") or [])
            }
            for lg in group.get("logs") or []:
                bn = to_int(lg["block_number"])
                ts = ts_by_block.get(bn)
                if ts is None:
                    raise HyperSyncError(
                        f"HyperSync {chain} response has a log at block {bn} "
                        f"with no matching block timestamp — refusing to misdate."
                    )
                result.rows.append(
                    LogRow(
                        block_number=bn,
                        log_index=to_int(lg.get("log_index", 0)),
                        block_time=ts,
                        address=(lg.get("address") or "").lower(),
                        topic0=_lower(lg.get("topic0")),
                        topic1=_lower(lg.get("topic1")),
                        topic2=_lower(lg.get("topic2")),
                        topic3=_lower(lg.get("topic3")),
                        data=lg.get("data") or "0x",
                        transaction_hash=_lower(lg.get("transaction_hash")),
                    )
                )
        nxt = page.get("next_block")
        if nxt is None or to_int(nxt) <= cursor:
            break
        cursor = to_int(nxt)
    if cursor < end_exclusive and result.archive_height:
        raise HyperSyncError(
            f"HyperSync {chain} returned an incomplete range: pagination stopped "
            f"at block {cursor} < requested to_block {to_block} "
            f"(archive_height={result.archive_height})."
        )
    return result


def archive_height(chain: str, *, post: Callable[..., Any] = requests.post) -> int:
    """Current HyperSync-indexed chain head — a cheap zero-row probe."""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}
    body = {"from_block": 0, "to_block": 1, "logs": [], "field_selection": {"block": ["number"]}}
    return to_int(_execute(chain, body, headers, post).get("archive_height", 0) or 0)


def block_timestamp(chain: str, block: int, *, post: Callable[..., Any] = requests.post) -> int:
    """UNIX timestamp of a single ``block`` via HyperSync (cached on disk)."""
    ck = _key("block_ts", chain, block)
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}
    body = {
        "from_block": block,
        "to_block": block + 1,
        "include_all_blocks": True,
        "logs": [],
        "field_selection": {"block": ["number", "timestamp"]},
    }
    data = _execute(chain, body, headers, post)
    for group in data.get("data") or []:
        for b in group.get("blocks") or []:
            if to_int(b["number"]) == block:
                ts = to_int(b["timestamp"])
                _cache_put(ck, ts)
                return ts
    raise HyperSyncError(f"HyperSync {chain}: block {block} not returned")


def returnable_head(chain: str) -> tuple[int, int]:
    """Exact highest query-returnable block on ``chain`` and its timestamp.

    ``archive_height`` alone is not probe-safe: blocks within a few hundred of
    the head are frequently not yet query-returnable, and its 0 fallback would
    send a probe negative — this walks back to the real returnable head.
    """
    _STEP = 16
    high = archive_height(chain)
    above: int | None = None
    head_ts: int | None = None
    for _ in range(64):
        if high <= 0:
            break
        try:
            head_ts = block_timestamp(chain, high)
            break
        except HyperSyncError:
            above = high
            high -= _STEP
    if head_ts is None:
        raise HyperSyncError(f"returnable_head({chain}): no returnable block near head")
    if above is not None and above - high > 1:
        lo, hi = high, above
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            try:
                block_timestamp(chain, mid)
                lo = mid
            except HyperSyncError:
                hi = mid
        high = lo
        head_ts = block_timestamp(chain, high)
    return high, head_ts


def find_block_at_or_before(chain: str, target_ts: int) -> int:
    """Highest block on ``chain`` whose timestamp <= ``target_ts`` (unix, UTC).

    Binary search over HyperSync block timestamps. A resolution where the
    archive head is at/behind the target is a provisional head-clamp (changes
    as the archive catches up) and is NOT cached; ranges the archive fully
    covers are cached (result is final).
    """
    high, head_ts = returnable_head(chain)
    if head_ts <= target_ts:
        _LOG.warning(
            "find_block_at_or_before(%s, ts=%d): archive head (block %d, ts %d) "
            "is at/behind the target — returning head WITHOUT caching.",
            chain, target_ts, high, head_ts,
        )
        return high
    ck = _key("blk_at_or_before", chain, target_ts)
    hit = _cache_get(ck)
    if hit is not None:
        return hit
    if block_timestamp(chain, 0) > target_ts:
        raise HyperSyncError(
            f"find_block_at_or_before({chain}, ts={target_ts}): target precedes genesis."
        )
    low = 0
    while low < high:
        mid = (low + high + 1) // 2
        if block_timestamp(chain, mid) <= target_ts:
            low = mid
        else:
            high = mid - 1
    _cache_put(ck, low)
    return low


# /query is a read-only, idempotent POST — transient upstream failures (LB
# resets, 5xx, 429) are retried with exponential backoff instead of killing a
# multi-minute chunk at its last fetch.
_RETRIES = 5
_BACKOFF_S = 2.0
_RETRY_STATUSES = {429, 500, 502, 503, 504}


def _execute(chain: str, body: dict[str, Any], headers: dict[str, str], post) -> dict[str, Any]:
    for attempt in range(_RETRIES + 1):
        try:
            resp = post(endpoint(chain), json=body, headers=headers, timeout=_DEFAULT_TIMEOUT)
        except requests.RequestException as exc:
            if attempt == _RETRIES:
                raise HyperSyncError(f"HyperSync request failed: {exc}") from exc
            logging.getLogger(__name__).warning(
                "HyperSync %s request error (%s) — retry %d/%d", chain, exc, attempt + 1, _RETRIES)
            time.sleep(_BACKOFF_S * 2 ** attempt)
            continue
        if resp.status_code in _RETRY_STATUSES and attempt < _RETRIES:
            logging.getLogger(__name__).warning(
                "HyperSync %s -> HTTP %d — retry %d/%d", chain, resp.status_code, attempt + 1, _RETRIES)
            time.sleep(_BACKOFF_S * 2 ** attempt)
            continue
        if not resp.ok:
            raise HyperSyncError(f"HyperSync {chain} -> HTTP {resp.status_code}: {resp.text[:400]}")
        data: dict[str, Any] = resp.json()
        return data
    raise HyperSyncError("unreachable")  # pragma: no cover
