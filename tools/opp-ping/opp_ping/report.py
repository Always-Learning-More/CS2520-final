"""Tidy (long-format) CSV of the demo's probe-budget sweep, for graphing.

One row per topology x target (furthest/closest) x injected failure x method x
probes per task (1, 2, 4, ... 256), averaged over every demo run (default 50).
The columns line up with the paper's figures:

  detection_recall  Figure 8: fraction of runs in which the method detected
                    the failure with this many probes per task
  localization_f1   Figure 9: F1 score of localization over the runs, from
                    true positives (right place named), false positives
                    (a wrong place named) and false negatives (right place
                    not named): F1 = 2TP / (2TP + FP + FN)
  path_coverage_pct Figure 6: share of the equal-cost paths covered
  switch_packets_per_probe   network overhead (Figure 7 measures the same
                    probe+ACK receptions per switch)

Methods: OPP_Sample_0 / OPP_Sample_10 / OPP_Sample_100 (USM sampling rate),
R-Pingmesh (Ping + Traceroute), RD-Probe (Ping + mirroring), Ping.
"""

import csv

KEY = ("architecture", "target", "failure", "method", "probes_per_task")
CONSTANT = ["architecture", "size", "hosts", "switches", "links",
            "source_ip", "source_device", "target",
            "failure", "failure_category", "method", "usm_rate_pct", "probes_per_task"]
DISTINCT = [("distinct_targets", "target_ip"), ("distinct_injected_links", "injected_link")]
AVERAGED = ["switch_hops", "equal_cost_paths", "blast_radius_hosts", "probes_sent",
            "path_coverage_pct", "avg_rtt_us", "switch_packets_per_probe",
            "recirculations_per_probe"]
# per-run 0/1 outcomes that become rates (and the F1 score) when averaged
OUTCOMES = ["detected", "localized", "wrong", "false_positive"]
COLUMNS = (CONSTANT + ["runs"] + [name for name, _ in DISTINCT] + AVERAGED
           + ["detection_recall", "localization_tp_rate", "localization_fp_rate",
              "localization_f1", "false_positive_rate"])

LOSS_KINDS = ("acl-drop", "link-flap", "port-down")


def _r(x, nd=4):
    return "" if x is None else round(x, nd)


def context_row(topo, rt, src, dst, target, fail):
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
        "injected_link": fail.link_text() if fail.kind != "none" else "",
        "blast_radius_hosts": len(lost) if fail.kind != "none" else "",
    }


def _outcome(healthy, detected, localized, wrong, any_report):
    if healthy:
        return {"detected": "", "localized": "", "wrong": "", "false_positive": int(any_report)}
    return {"detected": int(detected), "localized": int(localized), "wrong": int(wrong),
            "false_positive": ""}


def opp_budget_row(ctx, name, rate, fail, results, n):
    """OPP outcome with the first n probes of a session."""
    healthy = fail.kind == "none"
    reported = any(r.findings for r in results)
    localized = any(r.verdict == "detected" for r in results)
    # "partial" means the right kind of failure in the wrong place - a wrong
    # location - except for loops, where it means "loop seen, culprit not yet
    # pinned", which names no place at all
    wrong_verdicts = ("wrong",) if fail.kind == "loop" else ("wrong", "partial")
    wrong = any(r.verdict in wrong_verdicts for r in results)
    acks = [r for r in results if r.ack is not None]
    row = dict(ctx)
    row.update({
        "method": name,
        "usm_rate_pct": _r(rate * 100, 1),
        "probes_per_task": n,
        "probes_sent": n + sum(len(r.loop_phase2) for r in results),
        "path_coverage_pct": _r(100.0 * max(r.path_count / float(r.expected_paths)
                                            for r in results), 2),
        "avg_rtt_us": _r(sum(r.rtt_ns for r in acks) / 1000.0 / len(acks), 3) if acks else "",
        "switch_packets_per_probe": _r(sum(r.network_packets() for r in results) / float(n), 2),
        "recirculations_per_probe": _r(sum(r.recirculations for r in results) / float(n), 2),
    })
    row.update(_outcome(healthy, reported, localized, wrong, reported))
    return row


def _rpingmesh_verdict(fail, rep):
    want = {"acl-drop": "loss", "link-flap": "loss", "port-down": "loss",
            "congestion": "congestion", "loop": "loop"}.get(fail.kind)
    ok = {fail.switch, fail.neighbor}
    if fail.kind == "loop" and fail.wrong_port is not None:
        ok.add(fail.topo.neighbor(fail.switch, fail.wrong_port))
    right = any(kind == want and sw in ok for kind, sw in rep.rpingmesh_links)
    wrong = any(not (kind == want and sw in ok) for kind, sw in rep.rpingmesh_links)
    return right, wrong


def _rdprobe_match(fail, culprit):
    if culprit is None:
        return False
    kind, link = culprit
    if fail.kind == "loop":
        return kind == "misroute" and link == (fail.switch, fail.wrong_port)
    want = "congestion" if fail.kind == "congestion" else "loss"
    return kind == want and link == fail.link


def baseline_budget_rows(ctx, topo, rt, fail, rep, n):
    """Rows for R-Pingmesh, RD-Probe and Ping from a baseline.run() with n pings."""
    healthy = fail.kind == "none"
    pings = rep.pings
    delivered = [s for s in pings if s.delivered and s.rtt_ns is not None]
    rtt = _r(sum(s.rtt_ns for s in delivered) / 1000.0 / len(delivered), 3) if delivered else ""
    coverage = _r(100.0 * rep.paths_seen / rep.paths_total, 2)
    ping_pkts = sum(s.fwd_rx + s.rev_rx for s in pings)
    trace_samples = [s for _, samples in (rep.trace or []) for s in samples]
    trace_pkts = sum(s.fwd_rx + s.rev_rx for s in trace_samples)
    mirror_pkts = sum(s.fwd_rx for s in pings)          # one mirrored copy per switch
    anomalous = bool(rep.anomalous)

    def row(method, sent, pkts):
        r = dict(ctx)
        r.update({"method": method, "usm_rate_pct": "", "probes_per_task": n,
                  "probes_sent": sent, "path_coverage_pct": coverage, "avg_rtt_us": rtt,
                  "switch_packets_per_probe": _r(pkts / float(sent), 2),
                  "recirculations_per_probe": ""})
        return r

    out = []
    sent = n + len(trace_samples)
    rp = row("R-Pingmesh", sent, ping_pkts + trace_pkts)
    right, wrong = _rpingmesh_verdict(fail, rep) if not healthy else (False, False)
    rp.update(_outcome(healthy, anomalous, right, wrong, bool(rep.rpingmesh) or anomalous))
    out.append(rp)

    rd = row("RD-Probe", n, ping_pkts + mirror_pkts)
    seen = [c for c in rep.rdprobe_samples if c is not None]
    match = any(_rdprobe_match(fail, c) for c in seen)
    bad = any(not _rdprobe_match(fail, c) for c in seen)
    rd.update(_outcome(healthy, bool(seen), match, bad, bool(seen)))
    out.append(rd)

    pg = row("Ping", n, ping_pkts)
    pg.update(_outcome(healthy, anomalous, False, False, anomalous))
    out.append(pg)
    return out


def aggregate(rows):
    """Average per-run rows into one row per KEY (see the module docstring).

    - CONSTANT columns are copied; DISTINCT columns count how many different
      targets / injected links the runs used.
    - AVERAGED columns are means over the runs that have a value.
    - detection_recall, localization_tp_rate/fp_rate and false_positive_rate
      are the fractions of runs with that outcome; localization_f1 is computed
      from the summed counts, not averaged per run.
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
        if agg["failure"] == "none":
            fp = [r["false_positive"] for r in runs]
            agg.update({"detection_recall": "", "localization_tp_rate": "",
                        "localization_fp_rate": "", "localization_f1": "",
                        "false_positive_rate": round(sum(fp) / float(len(fp)), 4)})
        else:
            tp = sum(r["localized"] for r in runs)
            fp = sum(r["wrong"] for r in runs)
            fn = len(runs) - tp
            agg.update({
                "detection_recall": round(sum(r["detected"] for r in runs) / float(len(runs)), 4),
                "localization_tp_rate": round(tp / float(len(runs)), 4),
                "localization_fp_rate": round(fp / float(len(runs)), 4),
                "localization_f1": round(2.0 * tp / (2 * tp + fp + fn), 4) if (tp + fp + fn) else 0.0,
                "false_positive_rate": "",
            })
        out.append(agg)
    return out


def write_csv(path, rows):
    """Write aggregated rows to path, replacing any existing file."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in COLUMNS})


def probes_needed(agg_rows, metric="localization_f1", threshold=0.95):
    """{(architecture, target, failure, method): smallest probes_per_task whose
    metric reaches threshold, or None}."""
    best = {}
    for r in agg_rows:
        if r["failure"] == "none" or r[metric] == "":
            continue
        key = (r["architecture"], r["target"], r["failure"], r["method"])
        best.setdefault(key, None)
        if r[metric] >= threshold:
            n = r["probes_per_task"]
            if best[key] is None or n < best[key]:
                best[key] = n
    return best


def overall_by_budget(agg_rows, metric):
    """{(method, probes_per_task): mean metric over every architecture, target
    and failure case}."""
    cells = {}
    for r in agg_rows:
        if r["failure"] == "none" or r[metric] == "":
            continue
        cells.setdefault((r["method"], r["probes_per_task"]), []).append(r[metric])
    return {k: sum(v) / len(v) for k, v in cells.items()}

