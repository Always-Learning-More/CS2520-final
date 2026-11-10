#!/usr/bin/env python3
"""Reproduce every figure CSV (Figures 5, 6, 7-8, 9, 10, 12, 13/Table 7, 14).

Runs the existing per-figure runners one after another, cheapest first,
with one shared --jobs value. It does not change what any runner does; it only
builds ns-3 once up front, launches the runners, saves a log per step, and at
the end lists every results CSV that was written and any simulations that
failed.

    python3 experiment_reproduction/run_all_figures.py --jobs 8
    python3 experiment_reproduction/run_all_figures.py --only 6 12
    python3 experiment_reproduction/run_all_figures.py --skip 7-8 --jobs 4
    python3 experiment_reproduction/run_all_figures.py --no-run   # dry run

On WSL / Ubuntu 22.04+ run it through the container: wsl/run_all_figures.sh
"""

import argparse
import datetime
import os
import pathlib
import re
import subprocess
import sys
import time

EXP_DIR = pathlib.Path(__file__).resolve().parent
SIM_DIR = EXP_DIR.parent / "simulation"
sys.path.insert(0, str(EXP_DIR))
from runner_path_utils import PYTHON2, require_python2  # noqa: E402

# Cheapest first, so early figures finish even if a long one is interrupted.
FIGURE_ORDER = ["10", "12", "13", "6", "9", "14", "5", "7-8"]

FAILURE_PATTERN = re.compile(r"status=(exception|failed|copy_source_failed|timeout)")


def build_steps(args):
    """Return a list of (figure, step_name, figure_dir, argv) to execute."""
    py = sys.executable
    jobs = str(args.jobs)
    no_run = ["--no-run"] if args.no_run else []
    steps = {
        "10": [("figure10" + p, "figure9-10",
                ["runner/run_figure10%s.py" % p]) for p in "abcd"],
        "12": [("figure12", "figure12", ["run_figure12.py", "--jobs", jobs] + no_run)],
        "13": [("figure13_table7", "figure13-table7",
                ["run_figure13.py", "--jobs", jobs] + no_run)],
        "6": [("figure6", "figure6", ["run_figure6.py", "--jobs", jobs] + no_run)],
        "9": [("figure9" + p, "figure9-10",
               ["runner/run_figure9%s.py" % p, "--jobs", jobs] + no_run) for p in "abcd"],
        "14": [("figure14", "figure14", ["run_figure14.py", "--jobs", jobs] + no_run)],
        # Same arguments as figure5/run_figure5.py, plus --jobs.
        "5": [
            ("figure5a_fattree", "figure5",
             ["runner/run_fattree_coverage.py", "--opp-multicast-mode", "USM",
              "--jobs", jobs, "--k64-jobs", str(min(args.jobs, 12))] + no_run),
            ("figure5b_leaf_spine", "figure5",
             ["runner/run_leaf_spine_coverage.py", "--opp-multicast-mode", "USM",
              "--jobs", jobs] + no_run),
            ("figure5c_2d_torus", "figure5",
             ["runner/run_2d_torus_coverage.py", "--opp-multicast-mode", "USM",
              "--jobs", jobs] + no_run),
            ("figure5d_3d_torus", "figure5",
             ["runner/run_3d_torus_coverage.py", "--opp-multicast-mode", "USM",
              "--jobs", jobs] + no_run),
        ],
        "7-8": [("figure7-8", "figure7-8",
                 ["run_abdc_global.py", "--jobs", jobs] + no_run)],
    }
    selected = [f for f in FIGURE_ORDER
                if (not args.only or f in args.only) and f not in args.skip]
    out = []
    for fig in selected:
        for name, fig_dir, argv in steps[fig]:
            out.append((fig, name, EXP_DIR / fig_dir, [py] + argv))
    return out


def run_logged(cmd, cwd, log_path, prefix):
    """Run cmd, streaming output to the terminal and to log_path.

    Returns (returncode, number_of_failed_simulation_tasks).
    """
    failures = 0
    with log_path.open("w") as log:
        log.write("$ (cd %s && %s)\n" % (cwd, " ".join(cmd)))
        log.flush()
        proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True,
                                bufsize=1)
        try:
            for line in proc.stdout:
                log.write(line)
                sys.stdout.write("[%s] %s" % (prefix, line))
                sys.stdout.flush()
                if FAILURE_PATTERN.search(line):
                    failures += 1
            proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait()
            raise
    return proc.returncode, failures


def build_ns3(log_dir):
    if not SIM_DIR.is_dir():
        sys.exit("error: simulator directory not found: %s" % SIM_DIR)
    if not (SIM_DIR / "build" / "c4che" / "_cache.py").exists():
        print("== configuring ns-3 (first run)", flush=True)
        rc, _ = run_logged([PYTHON2, "./waf", "configure"], SIM_DIR,
                           log_dir / "00_waf_configure.log", "configure")
        if rc != 0:
            sys.exit("error: './waf configure' failed; see %s" % (log_dir / "00_waf_configure.log"))
    print("== building ns-3 (incremental)", flush=True)
    rc, _ = run_logged([PYTHON2, "./waf", "build"], SIM_DIR,
                       log_dir / "00_waf_build.log", "build")
    if rc != 0:
        sys.exit("error: './waf build' failed; see %s" % (log_dir / "00_waf_build.log"))


def new_csvs(since):
    found = []
    for path in sorted(EXP_DIR.glob("figure*/results/*.csv")):
        if path.stat().st_mtime >= since:
            found.append(path)
    return found


def fmt_secs(secs):
    secs = int(secs)
    return "%dh%02dm%02ds" % (secs // 3600, secs % 3600 // 60, secs % 60)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                        help="concurrent ns-3 simulations per runner (default: CPU count)")
    parser.add_argument("--only", nargs="+", choices=FIGURE_ORDER, default=[],
                        help="run only these figures")
    parser.add_argument("--skip", nargs="+", choices=FIGURE_ORDER, default=[],
                        help="skip these figures")
    parser.add_argument("--no-run", action="store_true",
                        help="generate configs only (dry run); Figure 10 still runs")
    parser.add_argument("--no-build", action="store_true",
                        help="do not run './waf build' first")
    parser.add_argument("--stop-on-error", action="store_true",
                        help="stop at the first runner that exits with an error")
    parser.add_argument("--log-dir", type=pathlib.Path, default=None,
                        help="where to save per-step logs (default: experiment_reproduction/logs/<timestamp>)")
    args = parser.parse_args()
    if args.jobs <= 0:
        parser.error("--jobs must be positive")
    return args


def main():
    args = parse_args()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = (args.log_dir or EXP_DIR / "logs" / stamp).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    steps = build_steps(args)
    if not steps:
        sys.exit("nothing to run (check --only / --skip)")
    needs_sim = any(fig != "10" for fig, _, _, _ in steps) and not args.no_run
    if needs_sim:
        require_python2()
        if not args.no_build:
            build_ns3(log_dir)

    print("== %d steps, jobs=%d, logs in %s" % (len(steps), args.jobs, log_dir), flush=True)
    start_all = time.time()
    summary = []
    for index, (fig, name, fig_dir, cmd) in enumerate(steps, 1):
        print("\n== [%d/%d] %s" % (index, len(steps), name), flush=True)
        started = time.time()
        log_path = log_dir / ("%02d_%s.log" % (index, name))
        try:
            rc, failures = run_logged(cmd, fig_dir, log_path, name)
        except KeyboardInterrupt:
            print("\ninterrupted during %s; partial logs in %s" % (name, log_dir))
            return 130
        status = "ok" if rc == 0 and failures == 0 else (
            "error rc=%d" % rc if rc != 0 else "%d failed sims" % failures)
        summary.append((name, status, time.time() - started, log_path))
        if rc != 0 and args.stop_on_error:
            break

    print("\n== summary (total %s)" % fmt_secs(time.time() - start_all))
    for name, status, secs, log_path in summary:
        print("  %-22s %-18s %10s  %s" % (name, status, fmt_secs(secs), log_path.name))
    csvs = new_csvs(start_all)
    print("\n== result CSVs written this run (%d)" % len(csvs))
    for path in csvs:
        print("  " + str(path.relative_to(EXP_DIR.parent)))
    bad = [s for s in summary if s[1] != "ok"]
    if bad:
        print("\n%d step(s) had problems. Per-task errors are in each runner's work_dir "
              "(the path printed as work_dir=...); start with the step's log in %s"
              % (len(bad), log_dir))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
