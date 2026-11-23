"""opp-ping: OmniPath Ping from the command line.

    opp-ping ping 11.0.0.1                                # fat-tree k=8, furthest host
    opp-ping ping 11.0.11.1 --arch ot-purdue --target closest --failure port-down
    opp-ping ping 11.0.0.1 --failure congestion -c 5
    opp-ping ping 11.0.0.1 --arch 2d-torus --size 6 --failure loop --usm --trace
    opp-ping ping 11.0.0.1 --failure acl-drop --baseline 64
    opp-ping info                                         # every architecture
    opp-ping hosts --arch leaf-spine
    opp-ping failures
    opp-ping demo                                         # every failure on every architecture
"""

import argparse
import os
import random
import sys

from . import archinfo, baseline, failures, report, routing, session, sweep, topology

ARCH_CHOICES = list(topology.ARCHES)


# ------------------------------------------------------------------ helpers
def _build(arch, size):
    try:
        topo = topology.build(arch, size)
    except ValueError as exc:
        raise SystemExit("error: %s" % exc)
    return topo, routing.Routing(topo)


def _seed(args):
    if args.seed is None:
        args.seed = random.randrange(1, 10 ** 6)
    return random.Random(args.seed)


def _resolve_host(topo, text, what):
    try:
        node = topo.lookup(text)
    except ValueError as exc:
        raise SystemExit("error: %s %s: %s\n       valid host IPs are %s .. %s"
                         % (what, text, exc, topo.ip(0), topo.ip(topo.num_hosts - 1)))
    if not topo.is_host(node):
        raise SystemExit("error: %s %s is %s, a switch; probes start and end at hosts "
                         "(%s .. %s)" % (what, text, topo.name[node], topo.ip(0),
                                         topo.ip(topo.num_hosts - 1)))
    return node


def _us(ns):
    return "%.1f us" % (ns / 1000.0)


def _finding_text(topo, kind, link, text):
    where = topo.link_name(*link) if link else "-"
    if kind == "loop":
        return "LOOP (Is_Loop=1, reported link %s)" % where
    return {"loss": "LOSS", "congestion": "CONGESTION", "misroute": "MISROUTE"}[kind] + " " + where


def _usm_rate(args):
    if args.usm:
        return 1.0
    if args.native:
        return 0.0
    return args.usm_rate / 100.0


def _failure_params(args):
    return {"flap_prob": args.flap_prob / 100.0, "congestion_us": args.congestion_us,
            "loop_prob": args.loop_prob / 100.0}


def _print_kv(rows, indent="  "):
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        print("%s%-*s : %s" % (indent, width, k, v))


# ------------------------------------------------------------------- ping
def _hops_word(n):
    return "1 switch hop" if n == 1 else "%d switch hops" % n


def _ip_list(topo, hosts, limit=6):
    shown = ", ".join(topo.ip(h) for h in hosts[:limit])
    return shown + (" ... (%d total)" % len(hosts) if len(hosts) > limit else "")


def _target_rows(topo, rt, src, dst, label, candidates, dist):
    """Rows describing the chosen furthest/closest device and how to reach it."""
    if label == "given":
        what = "%s %s, given with --dst, %s away" % (
            topo.ip(dst), topo.name[dst], _hops_word(rt.switch_hops(src, dst)))
    else:
        what = "%s %s is the %s device: %s away%s" % (
            topo.ip(dst), topo.name[dst], label, _hops_word(dist - 1),
            "" if len(candidates) == 1 else
            ", picked at random among %d equally %s hosts (%s)"
            % (len(candidates), "far" if label == "furthest" else "close",
               _ip_list(topo, candidates)))
    return [
        ("target", what),
        ("route", rt.route_text(src, dst)),
        ("traverse", rt.traverse_text(src, dst)),
        ("equal-cost paths", "%d" % rt.path_count(src, dst)),
    ]


def _blast_radius(topo, rt, src, fail):
    if fail.kind == "none":
        return None
    lost = rt.cut_off_hosts(src, fail.switch, fail.neighbor)
    if not lost:
        return "if %s - %s went down, every host could still reach %s over alternative paths" % (
            topo.name[fail.switch], topo.name[fail.neighbor], topo.name[src])
    return "if %s - %s went down, %d host(s) would lose contact with %s: %s" % (
        topo.name[fail.switch], topo.name[fail.neighbor], len(lost), topo.name[src],
        ", ".join("%s %s" % (topo.name[h], topo.ip(h)) for h in lost[:6])
        + (" ..." if len(lost) > 6 else ""))


def cmd_ping(args):
    topo, rt = _build(args.arch, args.size)
    rng = _seed(args)
    src = _resolve_host(topo, args.ip, "source")
    if args.dst:
        dst = _resolve_host(topo, args.dst, "destination")
        if dst == src:
            raise SystemExit("error: source and destination are the same host")
        label, candidates, dist = "given", [dst], rt.hops(src, dst)
    else:
        label = args.target
        dst, candidates, dist = rt.pick_target(src, label, rng)
    try:
        fail = failures.place(args.failure, topo, rt, src, dst, rng, fail_at=args.fail_at,
                              fail_link=args.fail_link, **_failure_params(args))
    except ValueError as exc:
        raise SystemExit("error: %s" % exc)

    usm_rate = _usm_rate(args)
    print("OPP PING %s (%s) -> %s (%s)%s" % (topo.ip(src), topo.name[src], topo.ip(dst),
                                           topo.name[dst], "" if label == "given" else
                                           "  [%s device]" % label))
    rows = [("architecture", "%s: %d hosts, %d switches" % (topo.describe(), topo.num_hosts,
                                                         topo.num_nodes - topo.num_hosts))]
    rows += _target_rows(topo, rt, src, dst, label, candidates, dist)
    rows.append(("failure", fail.describe()))
    radius = _blast_radius(topo, rt, src, fail)
    if radius:
        rows.append(("blast radius", radius))
    rows += [
        ("USM sampling", "%.0f%% (%s)" % (usm_rate * 100, "USM on every probe" if usm_rate >= 1 else
                                            "native multicast only" if usm_rate <= 0 else
                                            "round-robin, one USM probe every %d" %
                                            -(-100 // max(int(round(usm_rate * 100)), 1)))),
        ("seed", "%d (reuse with --seed %d)" % (args.seed, args.seed)),
    ]
    if fail.kind != "none" and not rt.on_dag(fail.switch, src, dst):
        rows.append(("note", "the failed switch is NOT on this flow's equal-cost paths"))
    _print_kv(rows)
    print()

    sess = session.Session(topo, rt, fail, rng, src, dst, trace=args.trace)
    results = sess.run(args.count, usm_rate, int(args.interval_ms * 1e6))
    for r in results:
        _print_probe(topo, r, fail, args.trace)
    _print_stats(topo, rt, fail, src, dst, results)

    if args.baseline:
        print()
        print("--- host-generated probing baselines (%d sprayed pings) ---" % args.baseline)
        rep = baseline.run(topo, rt, fail, rng, src, dst, args.baseline)
        for line in baseline.summary_lines(topo, rep):
            print(line)
    return 0


def _print_probe(topo, r, fail, trace):
    if r.ack is None:
        head = "probe=%-3d %-6s no ACK (local timer expired)" % (r.index, r.mode)
    else:
        head = ("probe=%-3d %-6s rtt=%10s  paths %d/%d  recirc %4d  switch-pkts %4d"
                % (r.index, r.mode, _us(r.rtt_ns), r.path_count, r.expected_paths,
                   r.recirculations, r.network_packets()))
    reports = ", ".join(_finding_text(topo, *f) for f in r.findings) or "healthy"
    verdict = "" if fail.kind == "none" and r.verdict == "ok" else "  [%s]" % r.verdict
    print("%s  -> %s%s" % (head, reports, verdict))
    if trace:
        for line in r.trace:
            print("      " + line)
    for i, p in enumerate(r.loop_phase2, 1):
        rep = ", ".join(_finding_text(topo, *f) for f in p.findings) or "no unexpected link"
        print("      loop phase 2 (re-inject #%d, path-authorized USM): %s" % (i, rep))
        if trace:
            for line in p.trace:
                print("          " + line)
    if r.loop_phase2 or any(k == "misroute" for k, _, _ in r.findings):
        if r.misconfigured_switch is not None:
            print("      => routing misconfiguration localized to %s" % topo.name[r.misconfigured_switch])
        else:
            print("      => loop detected; misconfigured switch not pinned yet (misrouting is probabilistic)")


def _paths_word(n):
    return "1 equal-cost path" if n == 1 else "%d equal-cost paths" % n


def _print_stats(topo, rt, fail, src, dst, results):
    n = len(results)
    acks = [r for r in results if r.ack is not None]
    rtts = [r.rtt_ns for r in acks]
    full = sum(1 for r in acks if r.path_count == r.expected_paths)
    usm = sum(1 for r in results if r.usm)
    print()
    print("--- %s opp-ping statistics (%s) ---" % (topo.ip(dst), topo.describe()))
    print("%d probes (%d USM, %d native), %d ACKs, %d timeouts"
          % (n, usm, n - usm, len(acks), n - len(acks)))
    if rtts:
        print("rtt min/avg/max = %.1f/%.1f/%.1f us" % (min(rtts) / 1e3, sum(rtts) / len(rtts) / 1e3,
                                                     max(rtts) / 1e3))
    print("full path coverage in %d/%d probes (%s); avg switch packets "
          "per probe (probe+ACK) %.1f"
          % (full, n, _paths_word(rt.path_count(src, dst)),
             sum(r.network_packets() for r in results) / float(n)))
    counts = {}
    for r in results:
        for f in r.findings:
            key = _finding_text(topo, *f)
            counts[key] = counts.get(key, 0) + 1
    if counts:
        print("reports: " + ", ".join("%s x%d" % kv for kv in sorted(counts.items())))
    t = session.tally(results)
    if fail.kind == "none":
        print("verdict: %d/%d healthy, %d false positives" % (t.get("ok", 0), n, t.get("false-positive", 0)))
    else:
        print("verdict vs injected %s: detected+localized %d/%d, partial %d, missed %d, wrong %d"
              % (fail.kind, t.get("detected", 0), n, t.get("partial", 0), t.get("missed", 0),
                 t.get("wrong", 0)))


# ------------------------------------------------------------------- info
def _arches(args):
    if args.arch == "all":
        if args.size is not None:
            raise SystemExit("error: --size needs a single --arch")
        return [(a, None) for a in ARCH_CHOICES]
    return [(args.arch, args.size)]


def cmd_info(args):
    for i, (arch, size) in enumerate(_arches(args)):
        topo, rt = _build(arch, size)
        if i:
            print()
        print("== %s ==" % topo.describe())
        _print_kv(archinfo.summarize(topo, rt))
    return 0


def cmd_hosts(args):
    topo, _ = _build(args.arch, args.size)
    print("%-14s %-10s %-14s %s" % ("IP", "host", "attached to", "position"))
    for h in list(topo.hosts)[:args.limit]:
        pos = " ".join("%s=%s" % kv for kv in sorted(topo.coord[h].items()))
        print("%-14s %-10s %-14s %s" % (topo.ip(h), topo.name[h], topo.name[topo.attached_switch(h)], pos))
    if topo.num_hosts > args.limit:
        print("... %d more (use --limit)" % (topo.num_hosts - args.limit))
    return 0


def cmd_failures(args):
    print("%-11s %-13s %s" % ("type", "category", "description"))
    for kind in failures.FAILURE_TYPES:
        cat, desc = failures.FAILURE_INFO[kind]
        print("%-11s %-13s %s" % (kind, cat, desc))
    print("\nPlace a failure with --fail-at DEVICE[:PORT] or --fail-link A,B (names, ids or IPs);"
          "\notherwise it is put on the probed flow's paths like the paper does.")
    return 0


# ------------------------------------------------------------------- demo
DEFAULT_CSV = "opp_ping_demo.csv"
FULL_DEMO_RUNS = 50
F1_TARGET = 0.95

CASE_TITLES = {
    "none": "none (healthy)",
    "acl-drop": "acl-drop",
    "loop": "forwarding loop",
    "link-flap": "link-flap",
    "congestion": "congestion",
    "port-down": "port-down",
}


def demo_mode(args):
    """Return (runs, individual).

    A full demo (no --target, --arch, --src or --runs) repeats the sweep
    FULL_DEMO_RUNS times and prints only the averaged results. Narrowing it
    with any of those options makes it an individual run: one run, printed
    case by case. An explicit --runs N > 1 always averages.
    """
    narrowed = args.target is not None or args.arch != "all" or args.src is not None
    runs = args.runs if args.runs is not None else (1 if narrowed else FULL_DEMO_RUNS)
    return runs, runs == 1


def cmd_demo(args):
    import time
    _seed(args)
    runs, individual = demo_mode(args)
    if not 0 <= args.max_exp <= 12:
        raise SystemExit("error: --max-exp must be between 0 and 12")
    sizes = sweep.budgets(args.max_exp)
    targets = ["furthest", "closest"] if args.target in (None, "both") else [args.target]
    if args.csv is None:
        # averaged results go to the default CSV; an individual run writes a CSV
        # only when asked, so it never replaces the averaged file by accident
        args.csv = "" if individual else DEFAULT_CSV
    models = []
    for arch, size in _arches(args):
        topo, rt = _build(arch, size)
        src = _resolve_host(topo, args.src, "source") if args.src else archinfo.default_source(topo)
        models.append((arch, size, topo, rt, src))
    jobs = max(1, args.jobs or 1)
    budget_text = "%d..%d probes per task (%d budgets: 2^0..2^%d)" % (
        sizes[0], sizes[-1], len(sizes), args.max_exp)
    if individual:
        print("opp-ping demo (individual run): %s; methods %s; targets %s; seed %d"
              % (budget_text, ", ".join(sweep.METHODS), " + ".join(targets), args.seed))
    else:
        print("opp-ping demo: %d runs averaged; %s; methods %s; base seed %d; %d worker(s)"
              % (runs, budget_text, ", ".join(sweep.METHODS), args.seed, jobs))
    params = _demo_params(args)
    tasks = [(arch, size, src, tuple(targets), args.seed + run, args.max_exp, params)
             for run in range(runs) for arch, size, topo, rt, src in models]
    started = time.time()

    def progress(done, total):
        if not individual and sys.stderr.isatty():
            sys.stderr.write("\r  %d/%d run x architecture tasks (%.0f s)"
                             % (done, total, time.time() - started))
            sys.stderr.flush()

    results = sweep.run_all(tasks, jobs=jobs, progress=progress)
    if not individual and sys.stderr.isatty():
        sys.stderr.write("\r" + " " * 60 + "\r")
    rows = [row for task_rows, _ in results for row in task_rows]
    agg = report.aggregate(rows)
    if individual:
        for (arch, size, topo, rt, src), (_, cases) in zip(models, results):
            _print_individual(topo, rt, src, cases, sizes)
    else:
        for arch, size, topo, rt, src in models:
            _print_averaged(topo, src, agg, runs, targets, sizes)
        _print_overall(agg, runs, sizes)
    if args.csv:
        report.write_csv(args.csv, agg)
        print()
        print("wrote %d rows to %s, averaged over %d run(s) (one row per topology x target x "
              "failure x method x probes per task; any previous file was overwritten)"
              % (len(agg), args.csv, runs))
    return 0


def _demo_header(topo, src):
    print()
    print("=" * 100)
    print("%s | %d hosts, %d switches | source %s %s"
          % (topo.describe(), topo.num_hosts, topo.num_nodes - topo.num_hosts,
             topo.ip(src), topo.name[src]))
    print("=" * 100)


def _symbol(row):
    """One character per budget for an individual run."""
    if row.get("false_positive", "") != "":
        return "F" if row["false_positive"] else "."
    if row["localized"]:
        return "L" if not row["wrong"] else "l"
    if row["wrong"]:
        return "W"
    return "D" if row["detected"] else "."


def _print_individual(topo, rt, src, cases, sizes):
    _demo_header(topo, src)
    print("legend: L localized  l localized + a wrong report  W wrong place only  "
          "D detected, not localized  . nothing  F false positive (healthy network)")
    target = None
    for case in cases:
        if case["target"] != target:
            target = case["target"]
            dst = case["dst"]
            print()
            print("--- %s device from %s: %s %s ---" % (target.upper(), topo.ip(src),
                                                      topo.ip(dst), topo.name[dst]))
            _print_kv(_target_rows(topo, rt, src, dst, target, case["candidates"], case["dist"]),
                      indent="    ")
        kind = case["kind"]
        print()
        injected = "-" if kind == "none" else "%s (an outage there cuts off %s host(s))" % (
            case["failure"], case["blast"])
        print("  [%s -> %s] injected: %s" % (CASE_TITLES[kind], target, injected))
        print("    %-16s" % "probes per task" + "".join("%5d" % n for n in sizes))
        for method in sweep.METHODS:
            cells = "".join("%5s" % _symbol(case["outcomes"][(method, n)]) for n in sizes)
            print("    %-16s%s" % (method, cells))


def _print_averaged(topo, src, agg, runs, targets, sizes):
    """Per architecture: how many probes per task each method needs before its
    localization F1 (Figure 9) reaches F1_TARGET, per failure and target, plus
    the healthy network's worst false-positive rate."""
    mine = [r for r in agg if r["architecture"] == topo.arch]
    if not mine:
        return
    need = report.probes_needed(mine, "localization_f1", F1_TARGET)
    fp = {}
    for r in mine:
        if r["failure"] == "none":
            k = (r["target"], r["method"])
            fp[k] = max(fp.get(k, 0.0), r["false_positive_rate"])
    print()
    print("=" * 112)
    print("%s: %d hosts, %d switches, source %s %s -- averaged over %d runs"
          % (topo.describe(), topo.num_hosts, topo.num_nodes - topo.num_hosts,
             topo.ip(src), topo.name[src], runs))
    print("probes per task needed for localization F1 >= %.2f (Figure 9 metric); "
          ">%d = not reached by %d; healthy row = worst false-positive rate"
          % (F1_TARGET, sizes[-1], sizes[-1]))
    print("=" * 112)
    head, sub = "%-16s" % "", "%-16s" % "failure"
    width = 8
    for target in targets:
        sample = next(r for r in mine if r["target"] == target)
        title = "%s (%s, %s)" % (target, _plural(sample["switch_hops"], "sw hop"),
                                 _plural(sample["equal_cost_paths"], "path"))
        head += "| %-*s" % (width * len(sweep.METHODS), title)
        sub += "| " + "".join("%*s" % (width, _short(m)) for m in sweep.METHODS)
    print(head)
    print(sub)
    for kind in sweep.CASES:
        line = "%-16s" % CASE_TITLES[kind]
        for target in targets:
            cells = ""
            for m in sweep.METHODS:
                if kind == "none":
                    v = fp.get((target, m))
                    cells += "%*s" % (width, "-" if v is None else "%.2f" % v)
                elif m == "Ping":
                    cells += "%*s" % (width, "n/a")
                else:
                    n = need.get((topo.arch, target, kind, m))
                    cells += "%*s" % (width, (">%d" % sizes[-1]) if n is None else str(n))
            line += "| " + cells
        print(line)


def _short(method):
    return {"OPP_Sample_0": "OPP_0", "OPP_Sample_10": "OPP_10", "OPP_Sample_100": "OPP_100",
            "R-Pingmesh": "RPmesh", "RD-Probe": "RDProbe", "Ping": "Ping"}[method]


def _print_overall(agg, runs, sizes):
    for metric, title in (("detection_recall", "detection recall (Figure 8)"),
                          ("localization_f1", "localization F1 (Figure 9)")):
        table = report.overall_by_budget(agg, metric)
        print()
        print("--- overall %s vs probes per task, averaged over every architecture, "
              "target and failure, %d runs ---" % (title, runs))
        print("%-16s" % "probes per task" + "".join("%7d" % n for n in sizes))
        for m in sweep.METHODS:
            print("%-16s" % m + "".join("%7s" % ("%.2f" % table[(m, n)] if (m, n) in table
                                                 else "-") for n in sizes))


def _plural(x, word):
    text = _fmt_num(x)
    return "%s %s%s" % (text, word, "" if text == "1" else "s")


def _fmt_num(x):
    if x == "":
        return "-"
    return ("%d" % x) if float(x).is_integer() else ("%.1f" % x)


def _demo_params(args):
    return {"flap_prob": args.flap_prob / 100.0, "congestion_us": args.congestion_us,
            "loop_prob": args.loop_prob / 100.0}


# ---------------------------------------------------------------- parsing
def _size_arg(text):
    if text == "paper":
        return "paper"
    try:
        return int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("size must be an integer or 'paper'")


def _add_topology_args(p, allow_all=False):
    choices = ARCH_CHOICES + (["all"] if allow_all else [])
    p.add_argument("--arch", choices=choices, default="all" if allow_all else "fat-tree",
                   help="architecture (default: %s)" % ("all" if allow_all else "fat-tree"))
    p.add_argument("--size", "-k", type=_size_arg, default=None,
                   help="k for fat-tree/leaf-spine, n for n x n (x n) tori, chain length for "
                        "ot-purdue, switches per group (a) for dragonfly, spines per group (s) "
                        "for dragonfly-plus, or 'paper' for the paper's evaluated size. Every "
                        "model needs >= 50 hosts and >= 50 switches; defaults are the smallest "
                        "such sizes: fat-tree 8, leaf-spine 34, 2d-torus 8, 3d-torus 4, "
                        "ot-purdue 12, dragonfly 4, dragonfly-plus 4")


def _add_failure_tuning(p):
    p.add_argument("--flap-prob", type=float, default=20.0, help="link-flap drop %% (paper: 20)")
    p.add_argument("--congestion-us", type=float, default=500.0,
                   help="extra queueing delay in us (paper: 500)")
    p.add_argument("--loop-prob", type=float, default=50.0,
                   help="%% of unicast lookups misrouted by the loop failure (paper: 50)")


def build_parser():
    parser = argparse.ArgumentParser(prog="opp-ping", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("ping", help="probe the furthest (or a given) host with OPP")
    p.add_argument("ip", help="source host IP (11.X.Y.1), node id or host name")
    _add_topology_args(p)
    p.add_argument("--target", choices=["furthest", "closest"], default="furthest",
                   help="probe the furthest (default) or closest host; ties broken at random")
    p.add_argument("--dst", help="explicit destination host (overrides --target)")
    p.add_argument("--failure", choices=failures.FAILURE_TYPES, default="none")
    p.add_argument("--fail-at", metavar="DEVICE[:PORT]", help="switch (and egress port) to break")
    p.add_argument("--fail-link", metavar="A,B", help="break the link from device A toward device B")
    p.add_argument("-c", "--count", type=int, default=4, help="number of probes (default 4)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--usm-rate", type=float, default=10.0,
                   help="%% of probes using unicast-based simulated multicast (paper default 10)")
    g.add_argument("--usm", action="store_true", help="USM on every probe")
    g.add_argument("--native", action="store_true", help="native multicast only (no USM)")
    p.add_argument("--interval-ms", type=float, default=10.0, help="probe period (paper: 10 ms)")
    p.add_argument("--seed", type=int, default=None, help="random seed (printed if omitted)")
    p.add_argument("--trace", action="store_true", help="print the switch-by-switch event log")
    p.add_argument("--baseline", type=int, default=0, metavar="N",
                   help="also run N sprayed pings with R-Pingmesh*/RD-Probe* style diagnosis")
    _add_failure_tuning(p)
    p.set_defaults(func=cmd_ping)

    p = sub.add_parser("info", help="general information about the architectures")
    _add_topology_args(p, allow_all=True)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("hosts", help="list host IPs")
    _add_topology_args(p)
    p.add_argument("--limit", type=int, default=32)
    p.set_defaults(func=cmd_hosts)

    p = sub.add_parser("failures", help="list the failure types")
    p.set_defaults(func=cmd_failures)

    p = sub.add_parser("demo", help="probe-budget sweep (1..256 probes per task) of every "
                                    "failure on every architecture")
    _add_topology_args(p, allow_all=True)
    p.add_argument("--target", choices=["both", "furthest", "closest"], default=None,
                   help="which target(s) to probe; giving it (or --arch/--src) makes this an "
                        "individual run printed case by case")
    p.add_argument("--src", help="source host (default 11.0.0.1; 11.0.11.1 on ot-purdue)")
    p.add_argument("--runs", type=int, default=None,
                   help="runs to average (default: 50 for the full demo, 1 when --target, "
                        "--arch or --src narrow it); with 1 run every case is printed")
    p.add_argument("--max-exp", type=int, default=8,
                   help="largest budget is 2^MAX_EXP probes per task (default 8 -> 1..256)")
    p.add_argument("--jobs", type=int, default=os.cpu_count() or 1,
                   help="worker processes (default: all CPU cores; results do not depend on it)")
    p.add_argument("--csv", metavar="PATH", default=None,
                   help="CSV file for the averaged results, overwritten each time (default "
                        "opp_ping_demo.csv; individual runs write one only when --csv is given)")
    p.add_argument("--no-csv", dest="csv", action="store_const", const="",
                   help="do not write a CSV")
    p.add_argument("--seed", type=int, default=None)
    _add_failure_tuning(p)
    p.set_defaults(func=cmd_demo)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    if (getattr(args, "count", 1) < 1 or getattr(args, "probes", 1) < 1
            or (getattr(args, "runs", None) or 1) < 1 or getattr(args, "runs", None) == 0):
        raise SystemExit("error: probe and run counts must be >= 1")
    try:
        return args.func(args)
    except BrokenPipeError:            # e.g. piped into head
        return 0


if __name__ == "__main__":
    sys.exit(main())
