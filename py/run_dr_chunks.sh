#!/bin/bash
# Thin wrapper kept for muscle memory — the chunked runner is first-class now:
exec "$(dirname "$0")/../.venv/bin/python" "$(dirname "$0")/run_dr_pipeline.py" "$@"
