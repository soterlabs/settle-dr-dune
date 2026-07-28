# PRD: first-class chunked DR pipeline (split `run_dr_pipeline.py`)

**Status:** proposed (follow-up PR to #10). **Owner:** settlement/DR pipeline.
**Sizing:** ~half a day of code (mostly moves from the proven PR #10 scripts)
+ one full ~2.5–3h validation run on the production box.

## Problem

The monolithic `py/run_dr_pipeline.py` cannot complete a full-history run on
the 3.7GB production box:

- It concatenates **all targets of a source family into one process**
  (`pipeline.source_monthly` does `pd.concat` over up to 6 targets' leg
  frames) — the `susds_susdc` step was OOM-killed mid-run (2026-07-25), taking
  the interactive session down with it.
- `template_ab.transfer_legs` materializes **one Python dict per leg** before
  building the DataFrame. At psm3_base scale (3.09M legs) that is ~2–3GB of
  dict overhead alone; the chunk was OOM-killed again even in its own process,
  and only completed after switching to compact column arrays + 8-way
  user-hash sharding (`py/run_dr_chunk_psm3_base.py`).
- A failed run loses **all** progress: no checkpoints, not resumable.

PR #10 ships working-but-parallel scripts (`run_dr_chunk.py`,
`run_dr_chunk_psm3_base.py`, `run_dr_chunks.sh`) that already survived a full
26-chunk regeneration. This PRD is about retiring that duplication and making
the chunked design the one blessed path.

## Goals

1. `run_dr_pipeline.py` completes a full-history run on the 3.7GB box,
   unattended, with flat memory.
2. **Resumable**: a killed run (OOM, reboot, transient HyperSync 503) restarts
   and skips completed work.
3. **Zero methodology drift**: identical output to the monolithic path
   (monthly DR is additive across disjoint user sets — chunking must remain
   provably exact), and the Dune-parity fixture suite stays green.
4. One implementation: delete the scratchpad-lineage duplicates from PR #10
   (`run_dr_chunk*.py` logic folds into the package; `run_dr_chunks.sh`
   reduces to a thin invocation or disappears).

## Non-goals

- Changing any attribution methodology (aggregator programs, eligibility
  windows, TWA vs EOD — all settled elsewhere).
- Performance beyond "fits in memory" (no parallel chunk execution — the box
  is memory-bound, sequential is the point).
- Moving off the loop-only TWA engine (vectorized engine was evaluated and
  removed previously).

## Design

### (a) One target per subprocess, checkpointed (~150 lines, mostly moves)

- **Worker** `py/run_dr_chunk.py` (promoted, de-duplicated): computes ONE
  chunk's monthly DR and writes `hypersync-results/dr_full/chunk_<name>.csv`.
  Chunk registry derives from `run_source.SPECS` + `pipeline.SOURCE_MONTHLY`
  (family → reclass/conversion/sp-deployment), not a hand-maintained dict:
  every Template A/C/D/E target becomes a chunk automatically; the class-D
  holders (9001/4001) join the registry as first-class sources
  (`py/drhs/sources/holder.py`, intraday TWA per PR #10 `c63fe10`).
- **Orchestrator** `run_dr_pipeline.py`: for each chunk, spawn
  `subprocess.run([sys.executable, "run_dr_chunk.py", name, *shard_args])`;
  skip if the checkpoint CSV exists; on non-zero exit, log and continue
  (partial failures surface in the summary, exit code non-zero at the end).
  Then the existing `pipeline.combine` runs over the chunk CSVs
  (groupby-sum on `(month, blockchain, token, ref_code, source)` — additive
  because every grouping input is row-local).
- `--sources`, `--end`, `--out` keep their semantics; add `--fresh` (ignore
  checkpoints) and `--list` (print chunk plan).

### (b) Compact column arrays in `transfer_legs` (~30 lines, THE careful one)

Replace the `recs: list[dict]` accumulation in `template_ab.transfer_legs`
(and its reuse by Templates B/C/E) with per-column Python lists →
`pd.DataFrame(dict-of-columns)`. Expected ~5–10× reduction in peak build
memory; psm3_base likely fits unsharded.

**Gate:** the full parity fixture suite must stay byte-identical
(`py/tests/test_parity.py` — 10 fixtures) plus `test_synthetic.py` (20
tests). No behavior change of any kind is acceptable here; this is purely a
data-structure swap. Ship (b) as its own commit so a regression bisects
instantly.

### (c) `--shard k/N` user-hash sharding (~15 lines)

Worker flag filtering legs to `int(user_addr[2:10], 16) % N == k` before TWA.
Exact by per-user independence of the TWA engine. The orchestrator's chunk
plan shards any target whose (fetched-rows × legs) estimate exceeds a
threshold (start: psm3_base at N=4 post-(b), tune from the validation run).
Retire `run_dr_chunk_psm3_base.py` — with (b)+(c) the special-case lean
worker is redundant.

### (d) Optional, deferred: page-streaming fetch (~40 lines)

`hypersync.query_logs` accumulates every LogRow before returning (~1GB at
3–4M rows). A `row_callback` / generator variant would cap fetch-time RSS.
**Only build if** the validation run shows fetch-time RSS as the new binding
constraint after (b). Keep out of the first PR otherwise.

## Acceptance criteria

1. `pytest py/tests/` fully green (parity + synthetic + revenue) after (b).
2. Full-history run on the production box completes unattended:
   `python py/run_dr_pipeline.py` → all chunks, peak RSS < 2.5GB per process.
3. Kill-and-restart mid-run resumes without recomputation of finished chunks.
4. Output equivalence: `dr_monthly_combined.csv` from the new path matches the
   PR #10 chunk-CSV combine to float tolerance, and
   `py/build_dr_comparison.py` Checks stay green (venue set 133=133, 0
   unexpected per-month moves, aggregator totals unchanged).
5. `run_dr_chunk_psm3_base.py` deleted; `run_dr_chunks.sh` deleted or reduced
   to `exec python run_dr_pipeline.py "$@"`.

## Risks & mitigations

- **(b) silently changes legs** (ordering, dtype, NA handling of `ref_code`):
  parity suite is the gate; keep `ref_code` as nullable `Int64`/`object`
  exactly as today; commit separately.
- **Chunk registry drift** (a new target added to SPECS but not chunked):
  registry is derived, not duplicated — a unit test asserts every SPECS
  target maps to exactly one chunk.
- **Harness/session kills long runs**: orchestrator is designed to be run
  detached (`setsid nohup`) and is resumable regardless; document in README.
- **tmpfs scratch pressure**: checkpoints live under the repo
  (`hypersync-results/`), never `/tmp` (which is RAM on this box).

## Validation plan (one run, ~2.5–3h)

Fresh full-history run on the box → compare combined output vs the committed
PR #10 chunk CSVs (must match) → rebuild the comparison workbook → Checks tab
green → record peak RSS per chunk in the PR description.

## Validation results (2026-07-28, executed)

Fresh full-history run (28 chunk jobs), detached, **zero failures**, fully
resumable (one mid-run stop/retune/resume exercised the checkpoints).

- **Equivalence: exact.** 1,985 combined rows vs the legacy per-target
  chunks: identical totals ($45,455,787.90), zero rows beyond 1e-6 relative
  tolerance.
- **Workbook Checks: all green** after rebuild (venue set 133=133; 1,265
  per-month cells, 0 unexpected; aggregator totals to the cent).
- **Peak RSS**: every chunk ≤ 1,898MB (susds_eth) — inside the 2.5GB budget —
  EXCEPT the psm3_base shards at **2,765–2,917MB**. Measured cause: the
  ~2.2GB fetch-time LogRow floor (1.8M swaps + 3.7M transfers held before
  legs), which sharding cannot reduce. N=4 was tried first and peaked at
  3,357–3,400MB with swap-thrash (~75min/shard); N=8 is the shipped setting.
  **This makes deferred item (d) (page-streaming fetch) justified** as the
  next follow-up; more sharding is not.
- **Wall clock**: dominated by psm3_base's per-shard refetch (8 fetches of
  the same rows, ~45min/shard on slow HyperSync). Second follow-up: a worker
  **multi-shard mode** (one fetch → all N shard CSVs, as the retired lean
  worker did) would cut base's wall clock ~8x.
