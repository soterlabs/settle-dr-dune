# Persistent HyperSync log cache

**Why.** Event logs are immutable, yet every settlement re-downloaded the full
history from HyperSync — ~80% of the ~11h wall clock — and the 8 Base shards
each re-fetched the identical event set (the worker's hash-shard filter is
applied *after* the download). The cache persists every log query's result on
disk and serves later runs from it, fetching only blocks not yet covered. A
monthly settlement becomes "download one month of new blocks + local compute";
the 8 Base shards download once and read 7 times.

**What it is NOT.** No methodology change: balances are still replayed from
genesis every run (deltas → levels, window-global attribution — see the
"why full history" discussion in the PR). Only the *transport* of historical
events moves from network to disk.

## Mechanics (`py/drhs/logcache.py`, wired inside `hypersync.query_logs`)

- **Location**: `$DRHS_CACHE_DIR` (default `~/.cache/drhs`) under
  `logs/{chain}/{key}/` — `meta.json` + `seg_{from}_{to}.parquet` (zstd).
  Expect a few GB for the full history. Requires `pyarrow` (in the venv;
  there is no requirements manifest in this repo).
- **Key** = hash of chain + canonicalized selections + log fields. Any change
  to what is asked for — one more address, one more topic — is a *different*
  entry; there is no partial reuse and no way to serve the wrong query.
  Adding a venue to SPECS simply creates new entries.
- **Coverage** is one contiguous `[cached_from, cached_through]` window per
  entry. Segments must abut exactly; `load_meta` refuses (→ full refetch) on
  any gap, overlap, or missing file, and `append_segment` hard-errors rather
  than create a hole. Writes are atomic (tmp + rename).
- **Reorg safety**: only blocks ≥ ~1h below the archive head observed during
  the fetch are persisted (`SAFE_DEPTH_BLOCKS`, per chain); the young tail is
  re-fetched live every run until it ages past the depth.
- **Replay order** is the original fetch order (parquet preserves rows), so a
  cached run's row stream is identical to an uncached one.

## Trust model (ops decision 2026-09-02)

Trusted by default, with an escape hatch and on-demand verification:

- `--no-cache` on `run_dr_pipeline.py` / `run_dr_chunk.py` (or env
  `DRHS_NO_LOG_CACHE=1`) = the pre-cache, all-network behaviour, bit-for-bit.
- `py/verify_log_cache.py [--chain X] [--samples K]` re-fetches random cached
  block ranges live and diffs every field of every row. Any mismatch names
  the entry dir to delete (next run refetches it). Run it whenever assurance
  is wanted; there is no mandatory cadence.
- Tests/fixtures that inject a fake `post` bypass the cache automatically —
  fake responses are never persisted as chain truth.

`--fresh` wipes *checkpoints*, never the log cache — they answer different
questions ("is this month computed for this window" vs "what did the chain
say"). To force a clean slate of both: `--fresh --no-cache`.

## Validation

Landed with: the full unit suite (`py/tests/test_logcache.py` — exact replay,
safe-depth tail, upward/downward extension, key sensitivity, bypasses,
corruption refusal) plus an end-to-end reproduction: a `--fresh` pipeline run
with the cache (populating it as it went) reproduced the settled August 2026
`dr_monthly_combined.csv` byte-identically.
