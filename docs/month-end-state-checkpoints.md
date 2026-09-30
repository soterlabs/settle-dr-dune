# Month-end state checkpoints

Starting with the close of August 2026 (`2026-09-01` exclusive cutoff), the
chunked DR pipeline writes a complete per-job checkpoint under
`hypersync-results/dr_state/<cutoff>/`:

- `*.state.parquet`: closing balance and sticky last referral code for every
  holder, including zero-balance holders;
- `*.monthly.parquet`: that job's cumulative monthly DR through the cutoff;
- `*.json`: versioned metadata with a deterministic attribution-code and
  source-configuration fingerprint, published last so a partial or stale
  snapshot is never accepted.

For a normal first-of-month run, the worker looks for the immediately prior
month's complete triplet. If found, it creates synthetic opening legs at UTC
midnight and fetches only the new month's on-chain events. If any part is
missing, it logs the reason and safely performs a full replay. Writes use zstd
Parquet and atomic renames.

A changed methodology/configuration fingerprint also forces a full replay.
The same applies if an allowlisted reroute owner crosses the window-global
intermediary threshold during the new month: that classification is
retroactive, so reusing the old closing state would not equal a full replay.

Bootstrap the August 2026 state once:

```sh
.venv/bin/python py/run_dr_pipeline.py --end 2026-09-01 --fresh
```

Then calculate September using that state:

```sh
.venv/bin/python py/run_dr_pipeline.py --end 2026-10-01 --fresh
```

`--full-replay` is the audit escape hatch: it ignores prior state, recomputes
all history, and refreshes the ending snapshot. The three Skybase protocol
sources currently use this fallback because their correct legs depend on
Morpho/Pendle protocol state in addition to holder balance state; they are
small and are not the HyperSync bottleneck. All standard holder, farm,
Template A/B/C/E, synthetic-program, reroute, and custody chunks scan
incrementally. Li.Fi keeps its filtered origin-anchor scan at the eligibility
start because bridges have no safe maximum completion time; its target token
and destination-event scans remain incremental, and the origin logs are served
from the persistent local event cache.

Snapshots are deliberately supported only at first-of-month UTC boundaries.
Runs with another cutoff keep the pre-existing full-replay behavior.
