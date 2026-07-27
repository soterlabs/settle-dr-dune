#!/bin/bash
# Chunked full-history DR pipeline for memory-constrained boxes (3.7GB).
#
# The monolithic run_dr_pipeline.py OOMs at susds_susdc (it concatenates all
# targets' leg frames in one process). This runs ONE TARGET PER SUBPROCESS —
# monthly DR is exactly additive across disjoint user sets — with checkpoint
# CSVs under hypersync-results/dr_full/ (completed chunks are skipped, so the
# run is resumable). psm3_base (3.09M legs) additionally needs the lean
# 8-way user-hash-sharded worker (compact column arrays; the dict-per-leg
# path in transfer_legs is what OOMs).
#
# Run DETACHED (long; ~2.5-3h):  setsid nohup bash py/run_dr_chunks.sh &
# Then build the workbook:       .venv/bin/python py/build_dr_comparison.py
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/../.venv/bin/python"
run() { echo "=== chunk $* ($(date -u +%H:%M:%S)) ==="; $PY -u "$DIR/run_dr_chunk.py" "$@" || echo "!!! chunk $* FAILED (exit $?)"; }
for c in stusds farms susds_eth susdc_ethereum susdc_base susdc_arbitrum susdc_optimism susdc_unichain; do
  run $c
done
echo "=== psm3_base lean ($(date -u +%H:%M:%S)) ==="
$PY -u "$DIR/run_dr_chunk_psm3_base.py" || echo "!!! psm3_base_lean FAILED (exit $?)"
for c in psm3_arbitrum psm3_optimism psm3_unichain \
         sp_spUSDC_ethereum sp_spUSDC_avalanche_c sp_spUSDT_ethereum sp_spPYUSD_ethereum sp_spETH_ethereum \
         usds_aave_9001 usds_ref4001; do
  run $c
done
echo "=== all chunks done ($(date -u +%H:%M:%S)) ==="
