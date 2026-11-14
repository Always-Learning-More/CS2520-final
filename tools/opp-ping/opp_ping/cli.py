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
import random
import sys

from . import archinfo, baseline, failures, report, routing, session, topology

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
        return "if %s - %s went down, every host could still reach %s (redundant paths)" % (
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
DEMO_CASES = [
    ("none", None),
    ("acl-drop", None),
    ("loop", 1.0),           # with USM on every probe
    ("loop", 0.0),           # native multicast only: the loop stays hidden
    ("link-flap", None),
    ("congestion", None),
    ("port-down", None),
]


def cmd_demo(args):
    rng = _seed(args)
    usm_rate = args.usm_rate / 100.0
    targets = ["furthest", "closest"] if args.target == "both" else [args.target]
    if args.csv is None:
        args.csv = "opp_ping_demo_seed%d.csv" % args.seed
    rows = []
    print("opp-ping demo: %d OPP probes per case (USM %.0f%% unless noted), %d baseline pings, "
          "targets: %s, seed %d" % (args.probes, usm_rate * 100, args.baseline,
                                    " + ".join(targets), args.seed))
    for arch, size in _arches(args):
        topo, rt = _build(arch, size)
        src = _resolve_host(topo, args.src, "source") if args.src else archinfo.default_source(topo)
        entries, conc, _ = archinfo.op_cache(arch, list(topo.params.values())[0])
        print()
        print("=" * 100)
        print("%s | %d hosts, %d switches | source %s %s"
              % (topo.describe(), topo.num_hosts, topo.num_nodes - topo.num_hosts,
                 topo.ip(src), topo.name[src]))
        print("OP cache %d entries/switch (%.1f KB), %d in-flight probe(s) per source switch"
              % (entries, entries * archinfo.ENTRY_BYTES / 1024.0, conc))
        print("=" * 100)
        for target in targets:
            dst, candidates, dist = rt.pick_target(src, target, rng)
            print()
            print("--- %s device from %s: %s %s ---" % (target.upper(), topo.ip(src),
                                                      topo.ip(dst), topo.name[dst]))
            _print_kv(_target_rows(topo, rt, src, dst, target, candidates, dist), indent="    ")
            for kind, forced in DEMO_CASES:
                rows.extend(_demo_case(args, topo, rt, src, dst, target, kind, forced, usm_rate))
    if args.csv:
        report.write_csv(args.csv, rows)
        print()
        print("wrote %d rows to %s (one row per topology x target x failure x method)"
              % (len(rows), args.csv))
    return 0


def _demo_case(args, topo, rt, src, dst, target, kind, forced, usm_rate):
    # per-(arch, target, failure) seed: both loop cases share the same placement
    case_rng = random.Random("%d-%s-%s-%s" % (args.seed, topo.arch, target, kind))
    fail = failures.place(kind, topo, rt, src, dst, case_rng, **_demo_params(args))
    rate = usm_rate if forced is None else forced
    label = kind if forced is None else "%s (%s)" % (kind, "USM" if forced else "native only")
    results = session.Session(topo, rt, fail, case_rng, src, dst).run(args.probes, rate)
    variant = "default" if forced is None else ("usm" if forced else "native")
    ctx = report.context_row(args.seed, topo, rt, src, dst, target, fail, variant, rate)
    rows = [report.opp_row(ctx, topo, fail, results, _finding_text)]
    t = session.tally(results)
    first = next((r.index for r in results if r.verdict == "detected"), None)
    reports = {}
    for r in results:
        for f in r.findings:
            k = _finding_text(topo, *f)
            reports[k] = reports.get(k, 0) + 1
    rep_text = ", ".join("%s x%d" % kv for kv in sorted(reports.items())) or "none"
    print("  [%s -> %s] %s" % (label, target, failures.FAILURE_INFO[kind][1]))
    if kind == "none":
        print("    injected   : -")
        verdict = "%d/%d healthy, %d false positives" % (t.get("ok", 0), args.probes,
                                                          t.get("false-positive", 0))
    else:
        lost = rt.cut_off_hosts(src, fail.switch, fail.neighbor)
        print("    injected   : %s  (an outage there would cut off %d host%s)"
              % (fail.link_text(), len(lost), "" if len(lost) == 1 else "s"))
        verdict = "detected+localized %d/%d (first at probe %s), missed %d, wrong %d" % (
            t.get("detected", 0), args.probes, first if first else "-",
            t.get("missed", 0) + t.get("partial", 0), t.get("wrong", 0))
    full = sum(1 for r in results if r.path_count == r.expected_paths)
    print("    OPP        : %s | full coverage %d/%d" % (verdict, full, args.probes))
    print("    OPP reports: %s" % rep_text)
    mis = [r for r in results if r.misconfigured_switch is not None]
    if mis:
        print("    loop pinned: %s (%d/%d probes)"
              % (topo.name[mis[0].misconfigured_switch], len(mis), args.probes))
    if args.baseline and forced != 0.0:
        rep = baseline.run(topo, rt, fail, case_rng, src, dst, args.baseline)
        for line in baseline.summary_lines(topo, rep):
            if line.startswith(("R-Pingmesh", "RD-Probe", "Ping:")) or "paths covered" in line:
                print("    " + line.strip())
        rows.extend(report.baseline_rows(ctx, topo, rt, fail, rep))
    return rows


def _demo_params(args):
    return {"flap_prob": args.flap_prob / 100.0, "congestion_us": args.congestion_us,
            "loop_prob": args.loop_prob / 100.0}


# ---------------------------------------------------------------- parsing
def _add_topology_args(p, allow_all=False):
    choices = ARCH_CHOICES + (["all"] if allow_all else [])
    p.add_argument("--arch", choices=choices, default="all" if allow_all else "fat-tree",
                   help="architecture (default: %s)" % ("all" if allow_all else "fat-tree"))
    p.add_argument("--size", "-k", type=int, default=None,
                   help="k for fat-tree/leaf-spine, n for n x n (x n) tori, chain length "
                        "for ot-purdue. Every model needs >= 50 hosts and >= 50 switches; "
                        "defaults are the smallest such sizes: fat-tree 8, leaf-spine 34, "
                        "2d-torus 8, 3d-torus 4, ot-purdue 12")


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

    p = sub.add_parser("demo", help="run every failure type on every architecture")
    _add_topology_args(p, allow_all=True)
    p.add_argument("--probes", type=int, default=10, help="OPP probes per case (default 10)")
    p.add_argument("--target", choices=["both", "furthest", "closest"], default="both",
                   help="which target(s) to probe (default both)")
    p.add_argument("--src", help="source host (default 11.0.0.1; 11.0.11.1 on ot-purdue)")
    p.add_argument("--csv", metavar="PATH", default=None,
                   help="CSV file for the results (default opp_ping_demo_seed<SEED>.csv)")
    p.add_argument("--no-csv", dest="csv", action="store_const", const="",
                   help="do not write a CSV")
    p.add_argument("--usm-rate", type=float, default=10.0, help="%% USM probes (default 10)")
    p.add_argument("--baseline", type=int, default=32, metavar="N",
                   help="sprayed pings for the baselines per case (0 to skip; default 32)")
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
    if getattr(args, "count", 1) < 1 or getattr(args, "probes", 1) < 1:
        raise SystemExit("error: probe counts must be >= 1")
    try:
        return args.func(args)
    except BrokenPipeError:            # e.g. piped into head
        return 0


if __name__ == "__main__":
    sys.exit(main())
