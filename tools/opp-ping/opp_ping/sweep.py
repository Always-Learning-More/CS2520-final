"""Probe-budget sweep for the demo, modelled on the paper's Figures 8 and 9.

For every architecture x target (furthest/closest) x injected failure, each
method gets a budget of 1, 2, 4, ... 2^max_exp probes per task:

  OPP_Sample_0, OPP_Sample_10, OPP_Sample_100
      OPP with 0%, 10% and 100% of probes using USM (the paper's three curves)
  R-Pingmesh   N pings, then N Traceroute probes per TTL if anything looked wrong
  RD-Probe     N pings with per-switch mirroring
  Ping         N plain pings (detects, never localizes)

Each budget's outcome is read from the first N probes of one longer
simulation (256 probes / pings / Traceroute probes per TTL by default). That
is exactly "N probes per task" in the paper, and lets the sweep cost one
simulation per case instead of nine.

Per run and budget a row records:
  detected        the method reported *something* about the failure
                  (averaged over runs = the recall of Figure 8)
  localized       it named the injected link / misconfigured switch (true positive)
  wrong           it named a different place (false positive)
  false_positive  any report at all in the healthy network
The averaged CSV turns these into recall, true/false-positive rates and the
localization F1 score of Figure 9.
"""

import random

from . import baseline, failures, report, routing, session, topology

CASES = ("none", "acl-drop", "loop", "link-flap", "congestion", "port-down")
OPP_VARIANTS = (("OPP_Sample_0", 0.0), ("OPP_Sample_10", 0.1), ("OPP_Sample_100", 1.0))
BASELINES = ("R-Pingmesh", "RD-Probe", "Ping")
METHODS = tuple(name for name, _ in OPP_VARIANTS) + BASELINES


def budgets(max_exp):
    return [2 ** e for e in range(max_exp + 1)]


# topologies are rebuilt once per process (the demo may use several processes)
_MODELS = {}


def model(arch, size):
    key = (arch, size)
    if key not in _MODELS:
        topo = topology.build(arch, size)
        _MODELS[key] = (topo, routing.Routing(topo))
    return _MODELS[key]


def run_task(task):
    """One run of one architecture. ``task`` is a plain tuple so it can be
    sent to worker processes:
        (arch, size, src, targets, seed, max_exp, failure_params)
    Returns (rows, cases), where cases describes each case for printing.
    """
    arch, size, src, targets, seed, max_exp, params = task
    topo, rt = model(arch, size)
    sizes = budgets(max_exp)
    top = sizes[-1]
    rng = random.Random("%d-%s" % (seed, arch))
    rows, cases = [], []
    for target in targets:
        dst, candidates, dist = rt.pick_target(src, target, rng)
        for kind in CASES:
            case_rng = random.Random("%d-%s-%s-%s" % (seed, arch, target, kind))
            fail = failures.place(kind, topo, rt, src, dst, case_rng, **params)
            ctx = report.context_row(topo, rt, src, dst, target, fail)
            outcomes = {}
            for name, rate in OPP_VARIANTS:
                opp_rng = random.Random("%d-%s-%s-%s-%s" % (seed, arch, target, kind, name))
                results = session.Session(topo, rt, fail, opp_rng, src, dst).run(top, rate)
                for n in sizes:
                    row = report.opp_budget_row(ctx, name, rate, fail, results[:n], n)
                    rows.append(row)
                    outcomes[(name, n)] = row
            base_rng = random.Random("%d-%s-%s-%s-baselines" % (seed, arch, target, kind))
            pings = baseline.ping(topo, rt, fail, base_rng, src, dst, top)
            trace = None
            if baseline.anomalous(topo, rt, src, dst, pings):
                trace = baseline.traceroute(topo, rt, fail, base_rng, src, dst, top)
            for n in sizes:
                rep = baseline.run(topo, rt, fail, base_rng, src, dst, n, pings=pings,
                                   trace=trace)
                for row in report.baseline_budget_rows(ctx, topo, rt, fail, rep, n):
                    rows.append(row)
                    outcomes[(row["method"], n)] = row
            cases.append({"target": target, "dst": dst, "candidates": candidates,
                          "dist": dist, "kind": kind, "failure": fail.link_text(),
                          "blast": ctx["blast_radius_hosts"], "outcomes": outcomes})
    return rows, cases


def run_all(tasks, jobs=1, progress=None):
    """Run tasks serially or in a process pool; results keep task order."""
    if jobs <= 1 or len(tasks) <= 1:
        out = []
        for i, t in enumerate(tasks):
            out.append(run_task(t))
            if progress:
                progress(i + 1, len(tasks))
        return out
    from concurrent.futures import ProcessPoolExecutor
    out = [None] * len(tasks)
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(run_task, t): i for i, t in enumerate(tasks)}
        done = 0
        from concurrent.futures import as_completed
        for fut in as_completed(futures):
            out[futures[fut]] = fut.result()
            done += 1
            if progress:
                progress(done, len(tasks))
    return out
