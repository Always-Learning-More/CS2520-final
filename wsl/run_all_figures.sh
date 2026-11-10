#!/usr/bin/env bash
# Reproduce every figure CSV with one command, inside the WSL toolchain container.
#
#   wsl/run_all_figures.sh                 # everything, jobs = CPU count
#   wsl/run_all_figures.sh --jobs 8        # limit concurrency (saves RAM)
#   wsl/run_all_figures.sh --only 6 12     # just some figures
#   wsl/run_all_figures.sh --skip 7-8      # everything except Figures 7-8
#   wsl/run_all_figures.sh --no-run        # dry run: generate configs only
#
# Long runs: keep it alive after closing the terminal with
#   nohup wsl/run_all_figures.sh --jobs 8 > run_all.log 2>&1 &
#   tail -f run_all.log
# All options: wsl/run_all_figures.sh --help
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$SCRIPT_DIR/../NS3/OPP"
exec "$SCRIPT_DIR/opp.sh" python3 experiment_reproduction/run_all_figures.py "$@"
