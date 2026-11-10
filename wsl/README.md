# Running OmniPath-Ping on Windows (WSL Ubuntu)

These files let the repository be copied through Windows into WSL and run
there without changing the simulator, the experiments, or their results.

## Why a container is used for the ns-3 simulator

The simulator is ns-3.17. Its bundled build tool (`./waf`) only runs under
Python 2, and its C++ was written for gcc-5. Ubuntu 24.04/26.04 ship neither,
and porting ns-3.17 to Python 3 and gcc 15 would be a large change to the code.

`wsl/opp.sh` therefore runs the build and the experiment runners inside a small
Ubuntu 18.04 container (gcc-5, Python 2.7 for waf, Python 3.8 for the runners).
The repository is mounted into the container at the same path, so outputs land
in the normal `results/` folders and every printed path works in your WSL shell.

The Rust solver needs no container and runs natively.

## One-time setup

All commands run in the WSL Ubuntu terminal. WSL 2 is required for containers
(check from Windows PowerShell with `wsl -l -v`).

1. **Copy the repository into the Linux filesystem.** Working from `/mnt/c/...`
   is very slow for builds and breaks file permissions. Run this from wherever
   the copy currently is (for example `/mnt/c/Users/<you>/Downloads/OmniPath-Ping`):

   ```bash
   python3 wsl/prepare_wsl.py --copy-to ~/OmniPath-Ping
   cd ~/OmniPath-Ping
   ```

   If the repository is already inside WSL (e.g. `~/cs2520/OmniPath-Ping`), just run
   `python3 wsl/prepare_wsl.py` from its top-level directory.

   The script undoes what Windows does to a checkout: it converts CRLF line
   endings back to LF and restores executable bits. With `.git` present it
   restores the exact committed bytes and keeps genuine edits. It is safe to
   run again at any time, e.g. after copying more files over from Windows.

2. **Install a container engine** (either one works):

   ```bash
   sudo apt update && sudo apt install -y podman
   ```

   or Docker: `sudo apt install -y docker.io && sudo usermod -aG docker $USER`,
   then open a new WSL terminal. Docker Desktop's WSL integration also works.

3. **Build and smoke-test the simulator.** The first command builds the
   toolchain image (needs internet, a few minutes), then compiles ns-3:

   ```bash
   wsl/opp.sh build
   wsl/opp.sh smoke
   ```

   `smoke` runs a 2-node, 1-flow simulation and prints its flow-completion file.

## Running every figure with one command

```bash
cd ~/OmniPath-Ping
wsl/run_all_figures.sh --jobs 8
```

This builds ns-3 if needed, then runs Figures 10, 12, 13/Table 7, 6, 9, 14, 5
and 7-8 in that order (cheapest first) and finishes with a summary of each
step and the list of result CSVs written. Logs for every step are saved in
`NS3/OPP/experiment_reproduction/logs/<timestamp>/`. A step is marked as a
problem if its runner exits with an error or any simulation reports
`status=failed`/`status=exception`.

Useful options: `--only 6 12` (just those figures), `--skip 7-8` (everything
else; Figures 7-8 are by far the longest), `--no-run` (dry run), and
`--stop-on-error`. The full run takes many hours, so keep it alive after
closing the terminal with:

```bash
nohup wsl/run_all_figures.sh --jobs 8 > run_all.log 2>&1 &
tail -f run_all.log
```

## Running individual experiments

Prefix the usual commands with `wsl/opp.sh`. Run them from `NS3/OPP/`:

```bash
cd ~/OmniPath-Ping/NS3/OPP
../../wsl/opp.sh python3 experiment_reproduction/figure5/run_figure5.py
../../wsl/opp.sh python3 experiment_reproduction/figure6/run_figure6.py --jobs 8
../../wsl/opp.sh python3 experiment_reproduction/figure7-8/run_abdc_global.py --jobs 8
../../wsl/opp.sh python3 experiment_reproduction/figure9-10/run_all.py --jobs 8
../../wsl/opp.sh python3 experiment_reproduction/figure12/run_figure12.py --jobs 8
../../wsl/opp.sh python3 experiment_reproduction/figure13-table7/run_figure13.py --jobs 8
../../wsl/opp.sh python3 experiment_reproduction/figure14/run_figure14.py --jobs 8
```

`wsl/opp.sh shell` opens a shell inside the container if you prefer to run
several commands there directly.

Set `--jobs` to about the number of CPU cores WSL can use. WSL gets half of the
machine's RAM by default; the k64 fat-tree runs are memory hungry, so for long
runs raise the limit in `%UserProfile%\.wslconfig` on Windows, for example:

```ini
[wsl2]
memory=24GB
```

then run `wsl --shutdown` from PowerShell and reopen the terminal.

## Rust unit-merge solver (native, no container)

```bash
sudo apt install -y cargo
cd ~/OmniPath-Ping/solver/unit-merge
cargo test --release
bash scripts/reproduce_six_tor_scope.sh            # all six examples
bash scripts/reproduce_six_tor_scope.sh fat-tree   # or a single one
```

## What was changed in the repository

- `.gitattributes`: keeps LF line endings even if the repository is cloned or
  edited on Windows; marks binary files (waf, pcap, images) so they are never
  converted.
- `wsl/`: this guide, `prepare_wsl.py`, `opp.sh`, and the toolchain `Dockerfile`.
- Experiment runners: the Python 2 interpreter used for `./waf` is read from
  `runner_path_utils.PYTHON2` (environment variable `OPP_PYTHON2`, default
  `python2`) instead of being hard-coded, and each runner stops immediately with
  an explanatory message if that interpreter is missing. Previously every
  simulation was silently reported as `status=exception`. Simulation logic,
  configs, inputs, and outputs are unchanged.
- Figure READMEs: corrected the run location to `NS3/OPP/`.

## Running natively instead (advanced)

If you have a Python 2.7 interpreter and a compiler that builds ns-3.17 on the
host, you can skip the container:
`export OPP_PYTHON2=/path/to/python2.7`, build with
`$OPP_PYTHON2 ./waf configure && $OPP_PYTHON2 ./waf build` in
`NS3/OPP/simulation`, then run the runners with plain `python3`.
