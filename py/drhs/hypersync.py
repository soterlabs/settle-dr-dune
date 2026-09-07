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
    # The tx's ``to`` (entrypoint), lower-cased — only when the query asked for
    # the transaction join (``with_tx_to=True``); None otherwise / in fixtures.
    tx_to: str | None = None


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
    with_tx_to: bool = False,
    post: Callable[..., Any] = requests.post,
    use_cache: bool | None = None,
) -> QueryResult:
    """Fetch all logs matching ``selections`` in ``[from_block, to_block]``
    (inclusive) — served from the persistent log cache where possible, with
    only uncovered blocks fetched live (drhs.logcache; disable via
    DRHS_NO_LOG_CACHE=1 or the pipeline's --no-cache for the pre-cache,
    all-network behaviour).

    ``use_cache`` is the explicit seam: True/False force it; the default None
    auto-selects — cached only for the default transport (an injected ``post``
    means a test/fixture whose fake responses must never persist as chain
    truth) with the env flag unset. Anything stubbing the network at a level
    the identity check cannot see (e.g. patching _query_logs_live) must pass
    use_cache=False or point DRHS_CACHE_DIR at a scratch dir.

    ``selections`` is HyperSync's ``logs`` array — each entry is
    ``{"address": [...], "topics": [[topic0...], [topic1...], ...]}``; multiple
    entries are OR'd. An injected ``post`` (tests, fixtures) always bypasses
    the cache: fake responses must never be persisted as chain truth.

    Contract note: ``QueryResult.archive_height`` reflects only the live
    fetches this call made — it is 0 for a fully-cached request. No caller
    reads it off query_logs today (they call ``archive_height()`` directly);
    keep it that way or probe explicitly. Cache writes are best-effort: a
    failed persist (disk full, etc.) logs a warning and the query still
    returns its rows.

    ``with_tx_to`` joins each log's transaction and fills ``LogRow.tx_to`` (the
    tx entrypoint) — same scan, a little more payload.
    """
    from drhs import logcache

    lf = log_fields or _DEFAULT_LOG_FIELDS
    if use_cache is None:
        use_cache = post is requests.post and logcache.enabled()
    if not use_cache:
        return _query_logs_live(chain, selections, from_block, to_block,
                                log_fields=log_fields, block_fields=block_fields,
                                with_tx_to=with_tx_to, post=post)
    if to_block < from_block:
        return QueryResult()  # degenerate range: empty, like the live path

    key = logcache.cache_key(chain, selections, lf, with_tx_to)
    d = logcache.entry_dir(chain, key)
    meta = logcache.load_meta(d)
    meta_args = {"chain": chain, "selections": selections, "log_fields": lf,
                 "with_tx_to": with_tx_to}
    depth = logcache.SAFE_DEPTH_BLOCKS.get(chain, logcache._DEFAULT_SAFE_DEPTH)
    result = QueryResult()

    # Coverage starts above the request: fill downward first — but only when
    # the request reaches coverage (to_block >= cached_from-1); a request
    # disjoint below is served live without the surplus gap-fetch (pre-cache
    # cost bound). Persist only a provably complete fetch (archive_height==0
    # skips _query_logs_live's incomplete-range guard, and a truncated
    # backfill would become a PERMANENT hole every later run replays);
    # depth-safety holds because the range sits below coverage persisted
    # under an earlier head.
    low_rows: list[LogRow] = []
    if meta is not None and from_block < meta.cached_from:
        low_to = meta.cached_from - 1 if to_block >= meta.cached_from - 1 else to_block
        low = _query_logs_live(chain, selections, from_block, low_to,
                               log_fields=log_fields, block_fields=block_fields,
                               with_tx_to=with_tx_to)
        result.archive_height = low.archive_height
        new_meta = None
        if low.archive_height > 0 and low_to == meta.cached_from - 1:
            new_meta = _persist(d, meta, meta_args, low.rows, from_block, low_to)
        if new_meta is not None:
            meta = new_meta
        else:
            low_rows = [r for r in low.rows if r.block_number <= to_block]

    if meta is not None:
        result.rows.extend(low_rows)  # below-coverage rows served live (rare)
        lo, hi = max(from_block, meta.cached_from), min(to_block, meta.cached_through)
        if hi >= lo:
            result.rows.extend(logcache.read_rows(d, meta, lo, hi))

    cov_hi = meta.cached_through if meta is not None else from_block - 1
    if to_block > cov_hi:
        live_from = max(from_block, cov_hi + 1)
        live = _query_logs_live(chain, selections, live_from, to_block,
                                log_fields=log_fields, block_fields=block_fields,
                                with_tx_to=with_tx_to)
        result.rows.extend(live.rows)
        result.archive_height = max(result.archive_height, live.archive_height)
        # Persist only blocks a safe depth below the head observed by THIS
        # fetch (archive_height==0 makes safe_hi negative -> nothing persists),
        # and only when the new segment abuts coverage — a request starting
        # above cached_through+1 is served live without extending the cache
        # (persisting it would hole the window; append_segment refuses).
        safe_hi = min(to_block, live.archive_height - depth)
        abuts = meta is None or live_from == meta.cached_through + 1
        if safe_hi >= live_from and abuts:
            # rows arrive block-ascending: cut, don't copy-scan the whole list
            from itertools import takewhile
            _persist(d, meta, meta_args,
                     list(takewhile(lambda r: r.block_number <= safe_hi, live.rows)),
                     live_from, safe_hi)
    return result


def _persist(d, meta, meta_args, rows, seg_from, seg_to):
    """Best-effort cache write: the rows are already in hand, so a failed
    persist (disk full, broken parquet install, ...) must degrade to a
    warning, never fail the query. A half-written entry is safe: load_meta
    refuses any inconsistency and the next run refetches. Returns the new
    Meta, or None if the write failed."""
    from drhs import logcache
    try:
        return logcache.append_segment(d, meta, meta_args, rows, seg_from, seg_to)
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("log cache write failed for %s [%s,%s]: %s — serving "
                     "live, entry not extended", d, seg_from, seg_to, exc)
        return None


def _query_logs_live(
    chain: str,
    selections: list[dict[str, Any]],
    from_block: int,
    to_block: int,
    *,
    log_fields: list[str] | None = None,
    block_fields: list[str] | None = None,
    with_tx_to: bool = False,
    post: Callable[..., Any] = requests.post,
) -> QueryResult:
    """The raw network fetch — pages followed via ``next_block`` until
    ``to_block``. Always complete or raising; never partial."""
    lf = log_fields or _DEFAULT_LOG_FIELDS
    bf = block_fields or _DEFAULT_BLOCK_FIELDS
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {_token()}"}
    fs: dict[str, Any] = {"log": lf, "block": bf}
    if with_tx_to:
        fs["transaction"] = ["hash", "to"]
    base = {"logs": selections, "field_selection": fs}
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
            to_by_tx = {
                _lower(tx.get("hash")): _lower(tx.get("to"))
                for tx in (group.get("transactions") or [])
            } if with_tx_to else {}
            for lg in group.get("logs") or []:
                bn = to_int(lg["block_number"])
                ts = ts_by_block.get(bn)
                if ts is None:
                    raise HyperSyncError(
                        f"HyperSync {chain} response has a log at block {bn} "
                        f"with no matching block timestamp — refusing to misdate."
                    )
                if with_tx_to and _lower(lg.get("transaction_hash")) not in to_by_tx:
                    # same contract as the timestamp join: complete or raising,
                    # never a silent None that would be persisted under the
                    # join key and resolve an entrypoint program to nothing.
                    raise HyperSyncError(
                        f"HyperSync {chain} response has a log in tx "
                        f"{lg.get('transaction_hash')} with no matching transaction "
                        f"— tx join incomplete, refusing to persist tx_to=None."
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
                        tx_to=to_by_tx.get(_lower(lg.get("transaction_hash"))) if with_tx_to else None,
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


def block_at_or_genesis(chain: str, target_ts: int) -> int:
    """``find_block_at_or_before``, or 0 when ``target_ts`` precedes the chain's
    genesis (a 2024-09-01 start on a younger L2 scans from block 0, as Dune
    would). Every OTHER failure — transport, auth, head not returnable —
    propagates: a swallowed 5xx must never turn into a silent full-chain scan."""
    try:
        return find_block_at_or_before(chain, target_ts)
    except HyperSyncError as exc:
        if "precedes genesis" in str(exc):
            return 0
        raise


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
