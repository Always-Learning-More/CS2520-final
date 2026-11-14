"""Host-generated probing baselines under packet spraying (paper Section 6.1).

* Ping: each probe is a normal unicast packet; every switch sprays it to one
  random equal-cost port (via the possibly misconfigured unicast table).
* R-Pingmesh* (Ping + Traceroute): on loss, high latency or an unexpected hop
  count, runs Traceroute with as many probes per TTL as there were pings, and
  localizes with the paper's heuristics. Shows the path ambiguity problem:
  one TTL maps to a *set* of candidate switches.
* RD-Probe* (Ping + Mirror): every switch mirrors the probe, so each ping that
  happens to cross the failure reveals it exactly; the cost is coverage.
"""

import collections

from . import engine

TTL_DEFAULT = 64


class Sample(object):
    __slots__ = ("links", "delivered", "reply_lost", "lost_at", "rtt_ns", "qdelays", "responder",
                 "fwd_rx", "rev_rx")

    def __init__(self):
        self.fwd_rx = 0            # switches that received the probe
        self.rev_rx = 0            # switches that received the reply
        self.links = []            # [(switch, port), ...] egress links taken
        self.delivered = False
        self.reply_lost = False
        self.lost_at = None        # (switch, port) where the packet vanished
        self.rtt_ns = None
        self.qdelays = []          # queueing delay per egress link
        self.responder = None      # switch answering a TTL-limited probe


def _walk(topo, routing, failure, rng, src, dst, ttl):
    """Forward one sprayed packet; returns a Sample."""
    s = Sample()
    node, port = src, 0
    t = 0
    flow = (src, dst)
    while True:
        dropped, _, qdelay = failure.egress(node, port, True, flow, rng)
        if not topo.is_host(node):
            s.links.append((node, port))
            s.qdelays.append(qdelay)
        if dropped:
            s.lost_at = (node, port)
            return s
        t += qdelay + engine.TX_NS + engine.LINK_DELAY_NS
        nxt = topo.neighbor(node, port)
        if not topo.is_host(nxt):
            s.fwd_rx += 1
        if topo.is_host(nxt):
            if nxt != dst:
                s.lost_at = (node, port)
                return s
            s.delivered = True
            break
        ttl -= 1
        if ttl == 0:
            s.responder = nxt
            break
        choice = rng.choice(routing.ecmp_ports(nxt, dst))
        node, port = nxt, failure.unicast_override(nxt, choice, rng)
    # reply travels back along the reverse of the forward links
    back = t
    hop_nodes = [src, topo.attached_switch(src)] + [topo.neighbor(n, p) for n, p in s.links]
    for i in range(len(hop_nodes) - 1, 0, -1):
        a, b = hop_nodes[i], hop_nodes[i - 1]
        p = topo.port_of(a, b)
        dropped, _, _ = failure.egress(a, p, False, None, rng)
        if dropped:
            s.reply_lost = True
            s.lost_at = (a, p)
            return s
        if not topo.is_host(b):
            s.rev_rx += 1
        back += engine.TX_NS + engine.LINK_DELAY_NS
    s.rtt_ns = back
    return s


def ping(topo, routing, failure, rng, src, dst, count, ttl=TTL_DEFAULT):
    return [_walk(topo, routing, failure, rng, src, dst, ttl) for _ in range(count)]


def traceroute(topo, routing, failure, rng, src, dst, per_ttl):
    hops = routing.switch_hops(src, dst)
    table = []
    for ttl in range(1, hops + 1):
        samples = [_walk(topo, routing, failure, rng, src, dst, ttl) for _ in range(per_ttl)]
        table.append((ttl, samples))
    return table


def coupon_collector(n):
    """Expected number of random probes to see all n equally likely paths."""
    return n * sum(1.0 / i for i in range(1, n + 1))


def expected_hop_switches(topo, routing, src, dst):
    """Switches that may appear at each TTL under correct routing."""
    layers, frontier = [], [topo.attached_switch(src)]
    while frontier:
        layers.append(sorted(frontier))
        nxt = set()
        for s in frontier:
            for p in routing.ecmp_ports(s, dst):
                nb = topo.neighbor(s, p)
                if nb != dst:
                    nxt.add(nb)
        frontier = sorted(nxt)
    return layers


class BaselineReport(object):
    def __init__(self):
        self.pings = []
        self.paths_seen = 0
        self.paths_total = 0
        self.trace = None
        self.rpingmesh = []        # findings text
        self.rdprobe = []
        self.rpingmesh_links = []  # (kind, (switch, port) or switch)
        self.rdprobe_links = []
        self.first_fault_sample = None
        self.anomalous = []        # 1-based indices of pings that looked wrong
        self.rdprobe_samples = []  # per ping: (kind, link) the mirrors pinpoint, or None


def run(topo, routing, failure, rng, src, dst, count):
    rep = BaselineReport()
    rep.pings = ping(topo, routing, failure, rng, src, dst, count)
    rep.paths_total = routing.path_count(src, dst)
    rep.paths_seen = len({tuple(s.links) for s in rep.pings if s.delivered
                          and all(pt in routing.ecmp_ports(n, dst) for n, pt in s.links)})
    base_rtt = 2 * routing.hops(src, dst) * (engine.TX_NS + engine.LINK_DELAY_NS)
    expected_len = routing.switch_hops(src, dst)

    lost = [s for s in rep.pings if not s.delivered or s.reply_lost]
    slow = [s for s in rep.pings if s.rtt_ns and s.rtt_ns - base_rtt > engine.T_QDELAY_NS]
    odd = [s for s in rep.pings if s.delivered and len(s.links) != expected_len]
    bad = set(map(id, lost + slow + odd))
    rep.anomalous = [i + 1 for i, s in enumerate(rep.pings) if id(s) in bad]

    # ---------------- RD-Probe* (Ping + Mirror): exact per-sample visibility
    for i, s in enumerate(rep.pings):
        culprit = None
        if s.lost_at is not None and not s.reply_lost:
            culprit = ("loss", s.lost_at)
        elif s.delivered and len(s.links) != expected_len:
            for (n, p) in s.links:
                if p not in routing.ecmp_ports(n, dst):
                    culprit = ("misroute", (n, p))
                    break
        elif s.qdelays and max(s.qdelays) > engine.T_QDELAY_NS:
            j = s.qdelays.index(max(s.qdelays))
            culprit = ("congestion", s.links[j])
        rep.rdprobe_samples.append(culprit)
        if culprit and culprit not in rep.rdprobe_links:
            rep.rdprobe_links.append(culprit)
            if rep.first_fault_sample is None:
                rep.first_fault_sample = i + 1
    for kind, (n, p) in rep.rdprobe_links:
        rep.rdprobe.append("%s on %s" % (kind, topo.link_name(n, p)))

    # ---------------- R-Pingmesh* (Ping + Traceroute) heuristics
    if not (lost or slow or odd):
        return rep
    rep.trace = traceroute(topo, routing, failure, rng, src, dst, count)
    layers = expected_hop_switches(topo, routing, src, dst)
    for ttl, samples in rep.trace:
        expected = layers[ttl - 1] if ttl - 1 < len(layers) else []
        replies = collections.Counter(s.responder for s in samples if s.rtt_ns is not None)
        if lost and sum(replies.values()) < len(samples):
            counts = {sw: replies.get(sw, 0) for sw in expected}
            fewest = min(counts.values()) if counts else None
            blamed = sorted(sw for sw, c in counts.items() if c == fewest)
            if blamed:
                pick = rng.choice(blamed)
                rep.rpingmesh_links.append(("loss", pick))
                rep.rpingmesh.append("loss: TTL %d lost %d/%d probes, blames %s (fewest replies%s)"
                                     % (ttl, len(samples) - sum(replies.values()), len(samples),
                                        topo.name[pick],
                                        ", random among %d" % len(blamed) if len(blamed) > 1 else ""))
                lost = []
        if odd:
            unexpected = sorted(sw for sw in replies if sw not in expected)
            if unexpected:
                rep.rpingmesh_links.append(("loop", unexpected[0]))
                rep.rpingmesh.append("loop: TTL %d answered by off-path switch(es) %s"
                                     % (ttl, ", ".join(topo.name[u] for u in unexpected)))
                odd = []
        if slow and ttl > 1:
            prev = {}
            for s in rep.trace[ttl - 2][1]:
                if s.rtt_ns is not None:
                    prev.setdefault(s.responder, []).append(s.rtt_ns)
            cur = {}
            for s in samples:
                if s.rtt_ns is not None:
                    cur.setdefault(s.responder, []).append(s.rtt_ns)
            if prev and cur:
                prev_avg = sum(sum(v) for v in prev.values()) / sum(len(v) for v in prev.values())
                worst = max(cur, key=lambda sw: sum(cur[sw]) / len(cur[sw]))
                jump = sum(cur[worst]) / len(cur[worst]) - prev_avg
                if jump > engine.T_QDELAY_NS:
                    rep.rpingmesh_links.append(("congestion", worst))
                    rep.rpingmesh.append("congestion: RTT jumps %.0f us at TTL %d, blames link into %s"
                                         % (jump / 1000.0, ttl, topo.name[worst]))
                    slow = []
    return rep


def summary_lines(topo, rep):
    out = []
    n = len(rep.pings)
    recv = sum(1 for s in rep.pings if s.rtt_ns is not None and s.delivered)
    rtts = [s.rtt_ns for s in rep.pings if s.rtt_ns is not None and s.delivered]
    out.append("Ping: %d sent, %d received, %.1f%% loss" % (n, recv, 100.0 * (n - recv) / n if n else 0))
    if rtts:
        out.append("      rtt min/avg/max = %.1f/%.1f/%.1f us"
                   % (min(rtts) / 1000.0, sum(rtts) / len(rtts) / 1000.0, max(rtts) / 1000.0))
    out.append("      paths covered %d/%d (expected ~%.0f random pings to cover all: n*H(n))"
               % (rep.paths_seen, rep.paths_total, coupon_collector(rep.paths_total)))
    if rep.trace:
        out.append("Traceroute (%d probes per TTL):" % len(rep.trace[0][1]))
        for ttl, samples in rep.trace:
            c = collections.Counter(topo.name[s.responder] for s in samples if s.rtt_ns is not None)
            lostc = sum(1 for s in samples if s.rtt_ns is None)
            parts = ", ".join("%s x%d" % kv for kv in sorted(c.items()))
            out.append("      TTL %d: %s%s" % (ttl, parts or "-", "  (%d lost)" % lostc if lostc else ""))
    out.append("R-Pingmesh* (Ping+Traceroute): %s"
               % ("; ".join(rep.rpingmesh) if rep.rpingmesh else
                  ("no anomaly seen" if not rep.trace else "anomaly seen but not localized")))
    out.append("RD-Probe*   (Ping+Mirror):     %s%s"
               % ("; ".join(rep.rdprobe) if rep.rdprobe else "no faulty path sampled",
                  " (first seen at ping #%d)" % rep.first_fault_sample if rep.first_fault_sample else ""))
    return out

