"""Tidy (long-format) CSV of demo results for graphing.

The demo repeats every case for many runs (default 50) with different random
draws; the CSV holds one row per topology x target (furthest/closest) x
injected failure x method, averaged over those runs.
Methods:
  OPP         OmniPath Ping (this emulator)
  R-Pingmesh  Ping + Traceroute with R-Pingmesh*'s localization heuristics
  Ping        plain sprayed ping: detects anomalies, cannot localize
  RD-Probe    Ping + per-switch mirroring (RD-Probe*)

Blank cells mean "not applicable" (e.g. Ping never localizes, so its
localization columns are 0 and its packet counts exclude traceroute).
"""

import csv

from . import session

# Columns of the averaged CSV. Each row is one topology x target x failure x
# method, averaged over every demo run (default 50).
KEY = ("architecture", "target", "failure", "variant", "method")
CONSTANT = ["architecture", "size", "hosts", "switches", "links",
            "source_ip", "source_device", "target",
            "failure", "failure_category", "variant", "usm_rate_pct", "method"]
DISTINCT = [("distinct_targets", "target_ip"), ("distinct_injected_links", "injected_link")]
AVERAGED = ["switch_hops", "equal_cost_paths", "blast_radius_hosts",
            "probes_sent", "detected", "localized", "false_positive",
            "detection_rate", "localization_rate", "first_detection_at", "first_localization_at",
            "missed_probes", "wrong_reports",
            "path_coverage_pct", "full_coverage_rate",
            "avg_rtt_us", "max_rtt_us",
            "switch_packets_per_probe", "recirculations_per_probe"]
COLUMNS = CONSTANT + ["runs"] + [name for name, _ in DISTINCT] + AVERAGED

METHODS = ("OPP", "R-Pingmesh", "Ping", "RD-Probe")


def _r(x, nd=4):
    return "" if x is None else round(x, nd)


def context_row(topo, rt, src, dst, target, fail, variant, usm_rate):
    lost = rt.cut_off_hosts(src, fail.switch, fail.neighbor) if fail.kind != "none" else []
    return {
        "architecture": topo.arch,
        "size": list(topo.params.values())[0],
        "hosts": topo.num_hosts,
        "switches": topo.num_nodes - topo.num_hosts,
        "links": len(topo.links),
        "source_ip": topo.ip(src),
        "source_device": topo.name[src],
        "target": target,
        "target_ip": topo.ip(dst),
        "target_device": topo.name[dst],
        "switch_hops": rt.switch_hops(src, dst),
        "equal_cost_paths": rt.path_count(src, dst),
        "failure": fail.kind,
        "failure_category": fail.category,
        "variant": variant,
        "usm_rate_pct": _r(usm_rate * 100, 1),
        "injected_link": fail.link_text() if fail.kind != "none" else "",
        "blast_radius_hosts": len(lost) if fail.kind != "none" else "",
    }


def opp_row(ctx, topo, fail, results, finding_text):
    n = len(results)
    with_findings = [r for r in results if r.findings]
    localized = [r for r in results if r.verdict == "detected"]
    t = session.tally(results)
    acks = [r for r in results if r.ack is not None]
    rtts = [r.rtt_ns / 1000.0 for r in acks]
    reports = {}
    for r in results:
        for f in r.findings:
            k = finding_text(topo, *f)
            reports[k] = reports.get(k, 0) + 1
    healthy = fail.kind == "none"
    row = dict(ctx)
    row.update({
        "method": "OPP",
        "probes_sent": n + sum(len(r.loop_phase2) for r in results),
        "detected": "" if healthy else int(bool(with_findings)),
        "localized": "" if healthy else int(bool(localized)),
        "false_positive": int(bool(with_findings)) if healthy else "",
        "detection_rate": "" if healthy else _r(len(with_findings) / float(n)),
        "localization_rate": "" if healthy else _r(len(localized) / float(n)),
        "first_detection_at": with_findings[0].index if with_findings and not healthy else "",
        "first_localization_at": localized[0].index if localized and not healthy else "",
        "missed_probes": "" if healthy else t.get("missed", 0),
        "wrong_reports": t.get("false-positive", 0) if healthy else t.get("wrong", 0) + t.get("partial", 0),
        "path_coverage_pct": _r(100.0 * sum(r.path_count / float(r.expected_paths) for r in results) / n, 2),
        "full_coverage_rate": _r(sum(1 for r in results if r.path_count == r.expected_paths) / float(n)),
        "avg_rtt_us": _r(sum(rtts) / len(rtts), 3) if rtts else "",
        "max_rtt_us": _r(max(rtts), 3) if rtts else "",
        "switch_packets_per_probe": _r(sum(r.network_packets() for r in results) / float(n), 2),
        "recirculations_per_probe": _r(sum(r.recirculations for r in results) / float(n), 2),
        "diagnosis": "; ".join("%s x%d" % kv for kv in sorted(reports.items())),
    })
    return row


def _rpingmesh_correct(fail, rep):
    want = {"acl-drop": "loss", "link-flap": "loss", "port-down": "loss",
            "congestion": "congestion", "loop": "loop"}.get(fail.kind)
    ok = {fail.switch, fail.neighbor}
    if fail.kind == "loop" and fail.wrong_port is not None:
        ok.add(fail.topo.neighbor(fail.switch, fail.wrong_port))
    return any(kind == want and sw in ok for kind, sw in rep.rpingmesh_links)


def _rdprobe_match(fail, culprit):
    if culprit is None:
        return False
    kind, link = culprit
    if fail.kind == "loop":
        return kind == "misroute" and link == (fail.switch, fail.wrong_port)
    want = "congestion" if fail.kind == "congestion" else "loss"
    return kind == want and link == fail.link


def baseline_rows(ctx, topo, rt, fail, rep):
    """Rows for R-Pingmesh, Ping and RD-Probe from one baseline.run() report."""
    pings = rep.pings
    n = len(pings)
    healthy = fail.kind == "none"
    delivered = [s for s in pings if s.delivered and s.rtt_ns is not None]
    rtts = [s.rtt_ns / 1000.0 for s in delivered]
    anomalous = rep.anomalous
    ping_pkts = sum(s.fwd_rx + s.rev_rx for s in pings)
    trace_samples = [s for _, samples in (rep.trace or []) for s in samples]
    trace_pkts = sum(s.fwd_rx + s.rev_rx for s in trace_samples)
    mirror_pkts = sum(s.fwd_rx for s in pings)          # one mirrored copy per switch
    coverage = _r(100.0 * rep.paths_seen / rep.paths_total, 2)
    common = {
        "path_coverage_pct": coverage,
        "full_coverage_rate": "",
        "avg_rtt_us": _r(sum(rtts) / len(rtts), 3) if rtts else "",
        "max_rtt_us": _r(max(rtts), 3) if rtts else "",
        "recirculations_per_probe": "",
        "missed_probes": "" if healthy else n - len(anomalous),
    }

    def base(method):
        row = dict(ctx)
        row.update(common)
        row["method"] = method
        return row

    rows = []
    # R-Pingmesh*: pings, then traceroute when something looked wrong
    rp = base("R-Pingmesh")
    rp_ok = _rpingmesh_correct(fail, rep) if not healthy else False
    rp.update({
        "probes_sent": n + len(trace_samples),
        "detected": "" if healthy else int(bool(anomalous)),
        "localized": "" if healthy else int(rp_ok),
        "false_positive": int(bool(rep.rpingmesh)) if healthy else "",
        "detection_rate": "" if healthy else _r(len(anomalous) / float(n)),
        "localization_rate": "",
        "first_detection_at": anomalous[0] if anomalous and not healthy else "",
        "first_localization_at": (n + len(trace_samples)) if rp_ok else "",
        "wrong_reports": len(rep.rpingmesh) if healthy else (0 if rp_ok or not rep.rpingmesh else 1),
        "switch_packets_per_probe": _r((ping_pkts + trace_pkts) / float(n + len(trace_samples)), 2),
        "diagnosis": "; ".join(rep.rpingmesh),
    })
    rows.append(rp)
    # Plain ping: sees loss/latency/TTL anomalies, cannot say where
    pg = base("Ping")
    pg.update({
        "probes_sent": n,
        "detected": "" if healthy else int(bool(anomalous)),
        "localized": "" if healthy else 0,
        "false_positive": int(bool(anomalous)) if healthy else "",
        "detection_rate": "" if healthy else _r(len(anomalous) / float(n)),
        "localization_rate": "" if healthy else 0,
        "first_detection_at": anomalous[0] if anomalous and not healthy else "",
        "first_localization_at": "",
        "wrong_reports": len(anomalous) if healthy else 0,
        "switch_packets_per_probe": _r(ping_pkts / float(n), 2),
        "diagnosis": "%d/%d pings anomalous" % (len(anomalous), n),
    })
    rows.append(pg)
    # RD-Probe*: every switch mirrors, so a ping that crosses the failure pins it
    rd = base("RD-Probe")
    matches = [i + 1 for i, c in enumerate(rep.rdprobe_samples) if _rdprobe_match(fail, c)]
    seen = [i + 1 for i, c in enumerate(rep.rdprobe_samples) if c is not None]
    wrong = [c for c in rep.rdprobe_samples if c is not None and not _rdprobe_match(fail, c)]
    rd.update({
        "probes_sent": n,
        "detected": "" if healthy else int(bool(seen)),
        "localized": "" if healthy else int(bool(matches)),
        "false_positive": int(bool(seen)) if healthy else "",
        "detection_rate": "" if healthy else _r(len(seen) / float(n)),
        "localization_rate": "" if healthy else _r(len(matches) / float(n)),
        "first_detection_at": seen[0] if seen and not healthy else "",
        "first_localization_at": matches[0] if matches and not healthy else "",
        "missed_probes": "" if healthy else n - len(seen),
        "wrong_reports": len(seen) if healthy else len(wrong),
        "switch_packets_per_probe": _r((ping_pkts + mirror_pkts) / float(n), 2),
        "diagnosis": "; ".join(rep.rdprobe),
    })
    rows.append(rd)
    return rows


def aggregate(rows):
    """Average per-run rows into one row per KEY.

    - CONSTANT columns are copied (they are the same in every run).
    - DISTINCT columns count how many different target hosts / injected links
      the runs used (targets are random among equally far/close hosts).
    - AVERAGED columns are the mean over the runs that have a value. 0/1
      columns therefore become fractions of runs (e.g. detected = 0.9 means
      9 out of 10 runs detected the failure). first_*_at is averaged only over
      the runs where the event happened.
    """
    groups, order = {}, []
    for row in rows:
        key = tuple(row[k] for k in KEY)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)
    out = []
    for key in order:
        runs = groups[key]
        agg = {c: runs[0].get(c, "") for c in CONSTANT}
        agg["runs"] = len(runs)
        for name, source in DISTINCT:
            values = {r.get(source, "") for r in runs} - {""}
            agg[name] = len(values) if values else ""
        for col in AVERAGED:
            values = [float(r[col]) for r in runs if r.get(col, "") != ""]
            agg[col] = round(sum(values) / len(values), 4) if values else ""
        out.append(agg)
    return out


def write_csv(path, rows):
    """Write rows (already aggregated) to path, replacing any existing file."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in COLUMNS})


def localization_table(agg_rows):
    """{(architecture, method): mean 'localized'} over the failure cases."""
    cells = {}
    for r in agg_rows:
        if r["failure"] == "none" or r["variant"] == "native" or r["localized"] == "":
            continue
        cells.setdefault((r["architecture"], r["method"]), []).append(r["localized"])
    return {k: sum(v) / len(v) for k, v in cells.items()}
