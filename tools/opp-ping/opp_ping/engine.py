"""Event-driven emulator of the OmniPath Ping data plane.

Implements the switch logic of Section 4 / Appendix B of the paper:

* OP cache entry per (switch, Token_ID), refreshed by newer Token_Seq_No.
* Probe duplication at the first arrival of a distinct probe, either with
  native multicast (correct multicast group) or with unicast-based simulated
  multicast (USM: repeated unicast lookups + recirculation, 700 ns per pass).
* Probe deduplication for later copies, recording queueing delay and
  detecting loops from a Remaining_Hop_Count mismatch.
* ACK aggregation/deduplication until the egress bitmap is empty, then ACK
  duplication to every recorded ingress port.
* Entry expiration after Remaining_Hop_Count * T_expire, which reports a
  remaining egress port as the faulty link and generates the ACK.
* Host agent: per-probe local timer, failure diagnosis (Section 4.6) and
  two-phase forwarding-loop localization (Appendix C).

This is a functional model for exploring behaviour, not a replacement for the
ns-3 simulator: timing uses 1 us links, 400 Gbps serialization, 700 ns per
recirculation pass and no background traffic.
"""

import heapq
import itertools

T_EXPIRE_NS = 10 * 1000 * 1000          # 10 ms per remaining hop
T_QDELAY_NS = 100 * 1000                # 100 us congestion threshold
T_RECIRC = 1000                         # max USM iterations per switch
RECIRC_NS = 700                         # ingress 400 ns + egress 300 ns
LINK_DELAY_NS = 1000
PACKET_BYTES = 100
TX_NS = PACKET_BYTES * 8 // 400         # 2 ns at 400 Gbps
HOST_DELAY_NS = 0
TOKEN = 7                               # Token_ID used by the single probe task


class Probe(object):
    __slots__ = ("seq", "src", "dst", "rem", "prev_qdelay", "prev_link", "usm", "authorized")

    def __init__(self, seq, src, dst, rem, usm, authorized):
        self.seq, self.src, self.dst, self.rem = seq, src, dst, rem
        self.prev_qdelay, self.prev_link = 0, None
        self.usm, self.authorized = usm, authorized

    def copy(self):
        p = Probe(self.seq, self.src, self.dst, self.rem, self.usm, self.authorized)
        p.prev_qdelay, p.prev_link = self.prev_qdelay, self.prev_link
        return p


class Ack(object):
    __slots__ = ("seq", "path_count", "max_qdelay", "congested_link", "faulty_link", "is_loop")

    def __init__(self, seq, path_count=1, max_qdelay=0, congested_link=None,
                 faulty_link=None, is_loop=False):
        self.seq, self.path_count = seq, path_count
        self.max_qdelay, self.congested_link = max_qdelay, congested_link
        self.faulty_link, self.is_loop = faulty_link, is_loop


class Entry(object):
    """OP cache entry (Table 2)."""
    __slots__ = ("seq", "inputs", "outputs", "max_qdelay", "congested_link", "expire_at",
                 "rem", "is_loop", "faulty_link", "path_count", "expired", "done")

    def __init__(self, seq, in_port, outputs, probe, now):
        self.seq = seq
        self.inputs = {in_port}
        self.outputs = set(outputs)
        self.max_qdelay = probe.prev_qdelay
        self.congested_link = probe.prev_link
        self.expire_at = now + probe.rem * T_EXPIRE_NS
        self.rem = probe.rem
        self.is_loop = False
        self.faulty_link = None
        self.path_count = 0
        self.expired = False
        self.done = False


class ProbeResult(object):
    def __init__(self, seq, src, dst, usm, authorized, expected_paths):
        self.seq, self.src, self.dst = seq, src, dst
        self.usm, self.authorized = usm, authorized
        self.expected_paths = expected_paths
        self.sent_at = 0
        self.ack = None
        self.ack_at = None
        self.timed_out = False
        self.recirculations = 0
        self.switch_probe_rx = {}
        self.switch_ack_rx = {}
        self.drops = []                 # (time, node, port, packet, reason)
        self.trace = []
        self.findings = []
        self.loop_phase2 = []           # list of ProbeResult for Appendix C phase 2
        self.misconfigured_switch = None

    @property
    def rtt_ns(self):
        return None if self.ack_at is None else self.ack_at - self.sent_at

    @property
    def path_count(self):
        return self.ack.path_count if self.ack else 0

    @property
    def mode(self):
        return ("USM" if self.usm else "native") + ("+auth" if self.authorized else "")

    def network_packets(self):
        return sum(self.switch_probe_rx.values()) + sum(self.switch_ack_rx.values())


class OppNetwork(object):
    def __init__(self, topo, routing, failure, rng, trace=False):
        self.topo = topo
        self.routing = routing
        self.failure = failure
        self.rng = rng
        self.trace_enabled = trace
        self.now = 0
        self.cache = {}                 # switch -> Entry (single Token_ID)
        self._events = []
        self._ids = itertools.count()
        self._result = None

    # ------------------------------------------------------------- event loop
    def _at(self, t, fn, *args):
        heapq.heappush(self._events, (t, next(self._ids), fn, args))

    def _run(self):
        while self._events:
            t, _, fn, args = heapq.heappop(self._events)
            self.now = t
            fn(*args)

    def _log(self, text):
        if self.trace_enabled:
            self._result.trace.append("%12.3f us  %s" % (self.now / 1000.0, text))

    def _n(self, node):
        return self.topo.name[node]

    def _link(self, link):
        if link is None:
            return "-"
        node, port = link
        return self.topo.link_name(node, port)

    # ------------------------------------------------------------- the agent
    def probe(self, src, dst, seq, usm=True, authorized=False):
        """Inject one probe and run the network until it quiesces."""
        topo, routing = self.topo, self.routing
        res = ProbeResult(seq, src, dst, usm, authorized, routing.path_count(src, dst))
        self._result = res
        res.sent_at = self.now
        rem = routing.switch_hops(src, dst)
        p = Probe(seq, src, dst, rem, usm, authorized)
        self._log("%s sends probe seq=%d (%s) to %s, Remaining_Hop_Count=%d"
                  % (self._n(src), seq, res.mode, topo.ip(dst), rem))
        # One extra T_expire beyond the source ToR's own entry timer, so the
        # ToR's expiry-generated ACK always beats the host's local timer.
        self._at(self.now + (rem + 1) * T_EXPIRE_NS, self._host_timer, res)
        self._send(src, 0, p, self.now, is_probe=True)
        self._run()
        return res

    def _host_timer(self, res):
        if res.ack is None:
            res.timed_out = True
            self._log("%s local timer expired: no ACK" % self._n(res.src))

    def _host_receive(self, host, packet, is_probe):
        res = self._result
        if is_probe:
            if host != packet.dst:
                self._log("%s drops misrouted probe" % self._n(host))
                return
            ack = Ack(packet.seq, 1, packet.prev_qdelay, packet.prev_link)
            self._log("%s receives probe, replies ACK" % self._n(host))
            self._send(host, 0, ack, self.now + HOST_DELAY_NS, is_probe=False)
        else:
            if host == res.src and packet.seq == res.seq and res.ack is None and not res.timed_out:
                res.ack, res.ack_at = packet, self.now
                self._log("%s receives aggregated ACK: paths=%d maxQ=%.1fus congested=%s "
                          "faulty=%s loop=%d" % (self._n(host), packet.path_count,
                                                 packet.max_qdelay / 1000.0,
                                                 self._link(packet.congested_link),
                                                 self._link(packet.faulty_link), packet.is_loop))

    # --------------------------------------------------------- packet transfer
    def _send(self, node, port, packet, t, is_probe):
        """Packet leaves node:port at time t (applies failures on that link)."""
        res = self._result
        topo = self.topo
        flow = (packet.src, packet.dst) if is_probe else None
        dropped, reason, qdelay = self.failure.egress(node, port, is_probe, flow, self.rng)
        if is_probe and not topo.is_host(node):
            packet.prev_qdelay = qdelay
            packet.prev_link = (node, port)
            packet.rem -= 1
        kind = "probe" if is_probe else "ACK"
        if dropped:
            res.drops.append((t, node, port, kind, reason))
            self._log("DROP %s on %s (%s)" % (kind, topo.link_name(node, port), reason))
            return
        nbr = topo.neighbor(node, port)
        in_port = topo.port_of(nbr, node)
        arrive = t + qdelay + TX_NS + LINK_DELAY_NS
        if topo.is_host(nbr):
            self._at(arrive, self._host_receive, nbr, packet, is_probe)
        elif is_probe:
            self._at(arrive, self._switch_probe, nbr, in_port, packet)
        else:
            self._at(arrive, self._switch_ack, nbr, in_port, packet)

    # ------------------------------------------------------ probe processing
    def _switch_probe(self, sw, in_port, p):
        res = self._result
        res.switch_probe_rx[sw] = res.switch_probe_rx.get(sw, 0) + 1
        expected_rem = self.routing.dist_to(p.dst)[sw]
        if p.authorized and p.rem != expected_rem:
            res.drops.append((self.now, sw, in_port, "probe", "unauthorized switch"))
            self._log("%s drops probe: not authorized for this Token_ID (off expected path)"
                      % self._n(sw))
            return
        e = self.cache.get(sw)
        if (e is not None and p.seq == e.seq and e.rem != expected_rem
                and p.rem == expected_rem):
            # The entry was opened by a copy that took a detour (it arrived
            # first, with the wrong Remaining_Hop_Count). The switch checks the
            # hop count against its own distance to the destination (App. C,
            # note 3), so the on-path copy takes the entry over; the loop
            # evidence is kept.
            self._log("%s on-path copy replaces entry opened by a detoured copy -> Is_Loop=1"
                      % self._n(sw))
            self._new_entry(sw, in_port, p, is_loop=True)
            return
        if e is None or p.seq > e.seq:
            self._new_entry(sw, in_port, p)
        elif p.seq == e.seq and not e.expired:
            if p.rem != e.rem:
                # A copy that went around a loop: flag it, but do not treat its
                # ingress port as an upstream path (no ACK is sent back to it).
                if not e.is_loop:
                    self._log("%s dedup: Remaining_Hop_Count %d != %d -> Is_Loop=1"
                              % (self._n(sw), p.rem, e.rem))
                e.is_loop = True
                return
            e.inputs.add(in_port)
            if p.prev_qdelay > e.max_qdelay:
                e.max_qdelay, e.congested_link = p.prev_qdelay, p.prev_link
            self._log("%s dedup probe copy from port %d" % (self._n(sw), in_port))
            if e.done:
                # A late copy (e.g. delayed by congestion) arrives after the
                # downstream results were already aggregated and propagated:
                # answer it right away so the upstream switch is not left
                # waiting, carrying this copy's queueing delay as well.
                self._log("%s late copy after aggregation -> immediate ACK on port %d"
                          % (self._n(sw), in_port))
                ack = Ack(e.seq, e.path_count, e.max_qdelay, e.congested_link,
                          e.faulty_link, e.is_loop)
                self._send(sw, in_port, ack, self.now, is_probe=False)
        else:
            self._log("%s drops outdated probe" % self._n(sw))

    def _new_entry(self, sw, in_port, p, is_loop=False):
        res = self._result
        routing, topo = self.routing, self.topo
        candidate = routing.ecmp_ports(sw, p.dst)
        e = Entry(p.seq, in_port, candidate, p, self.now)
        e.is_loop = is_loop
        self.cache[sw] = e
        self._at(e.expire_at, self._expire, sw, e)
        if not p.usm:
            self._log("%s new entry; native multicast to ports %s"
                      % (self._n(sw), candidate))
            for port in candidate:
                self._send(sw, port, p.copy(), self.now, is_probe=True)
            return
        # Unicast-based simulated multicast (Figure 3)
        remaining = set(candidate)
        extra = set()
        iterations = 0
        t = self.now
        sent = []
        while remaining and iterations < T_RECIRC:
            looked_up = self.rng.choice(candidate)
            port = self.failure.unicast_override(sw, looked_up, self.rng)
            if port in remaining:
                remaining.discard(port)
                sent.append(port)
                self._send(sw, port, p.copy(), t, is_probe=True)
            elif port not in candidate and port not in extra:
                extra.add(port)
                e.outputs.add(port)
                sent.append(port)
                self._log("%s unicast lookup for port %d returned port %d (%s): misrouted copy"
                          % (self._n(sw), looked_up, port, self._n(topo.neighbor(sw, port))))
                self._send(sw, port, p.copy(), t, is_probe=True)
            iterations += 1
            if remaining:
                t += RECIRC_NS
        recirc = iterations - 1
        res.recirculations += max(recirc, 0)
        if remaining:
            self._log("%s USM hit T_recirc=%d with ports %s uncovered"
                      % (self._n(sw), T_RECIRC, sorted(remaining)))
        self._log("%s new entry; USM to ports %s (%d recirculations)"
                  % (self._n(sw), sent, max(recirc, 0)))

    # -------------------------------------------------------- ACK processing
    def _switch_ack(self, sw, in_port, a):
        res = self._result
        res.switch_ack_rx[sw] = res.switch_ack_rx.get(sw, 0) + 1
        e = self.cache.get(sw)
        if e is None or a.seq != e.seq or e.expired or e.done or in_port not in e.outputs:
            self._log("%s drops outdated/unexpected ACK on port %d" % (self._n(sw), in_port))
            return
        e.outputs.discard(in_port)
        e.path_count += a.path_count
        if a.max_qdelay > e.max_qdelay:
            e.max_qdelay, e.congested_link = a.max_qdelay, a.congested_link
        if a.faulty_link is not None:
            if e.faulty_link is None or self.rng.random() < 0.5:
                e.faulty_link = a.faulty_link
        if a.is_loop:
            e.is_loop = True
        if e.outputs:
            self._log("%s aggregates ACK from port %d, waiting for %s"
                      % (self._n(sw), in_port, sorted(e.outputs)))
            return
        self._propagate(sw, e, "all ACKs aggregated")

    def _propagate(self, sw, e, why):
        e.done = True
        self._log("%s %s (paths=%d) -> ACK to ports %s"
                  % (self._n(sw), why, e.path_count, sorted(e.inputs)))
        for port in sorted(e.inputs):
            ack = Ack(e.seq, e.path_count, e.max_qdelay, e.congested_link,
                      e.faulty_link, e.is_loop)
            self._send(sw, port, ack, self.now, is_probe=False)

    def _expire(self, sw, entry):
        e = self.cache.get(sw)
        if e is not entry or e.done:
            return
        e.expired = True
        port = self.rng.choice(sorted(e.outputs))
        e.faulty_link = (sw, port)
        self._log("%s entry expired with egress ports %s outstanding -> Faulty_Link=%s"
                  % (self._n(sw), sorted(e.outputs), self.topo.link_name(sw, port)))
        self._propagate(sw, e, "generates ACK for expired entry")


# ------------------------------------------------------------------ diagnosis
def diagnose(topo, routing, res):
    """Host-agent failure reports (Section 4.6). Returns list of (kind, link, text)."""
    findings = []
    src, dst = res.src, res.dst
    if res.ack is None:
        link = (src, 0)
        findings.append(("loss", link, "packet loss on %s (no ACK before local timer)"
                         % topo.link_name(src, 0)))
        return findings
    a = res.ack
    if a.is_loop:
        findings.append(("loop", a.faulty_link,
                         "forwarding loop / routing misconfiguration detected (Is_Loop=1)"))
    if a.faulty_link is not None:
        node, port = a.faulty_link
        node_on_path = routing.on_dag(node, src, dst)
        expected_link = port in routing.ecmp_ports(node, dst)
        if node_on_path and not expected_link:
            # Appendix C: an unexpected link out of an on-path switch pins the
            # routing misconfiguration on that switch.
            findings.append(("misroute", a.faulty_link,
                             "unexpected link %s: %s forwards off the equal-cost paths"
                             % (topo.link_name(node, port), topo.name[node])))
        elif not node_on_path:
            if not a.is_loop:
                findings.append(("loop", a.faulty_link,
                                 "probe copies observed off the expected paths"))
        elif not a.is_loop:
            findings.append(("loss", a.faulty_link,
                             "packet loss on %s" % topo.link_name(node, port)))
    if a.max_qdelay > T_QDELAY_NS:
        node, port = a.congested_link
        findings.append(("congestion", a.congested_link,
                         "congestion on %s (queueing delay %.1f us > %.0f us)"
                         % (topo.link_name(node, port), a.max_qdelay / 1000.0,
                            T_QDELAY_NS / 1000.0)))
    return findings


def localize_loop(net, res, first_seq, attempts=16):
    """Appendix C: pin the misconfigured switch of a detected loop.

    If the ACK already reports an unexpected link out of an on-path switch,
    that switch is the culprit. Otherwise (phase 2) re-inject the probe with
    path authorization, so switches off the expected paths drop it and the
    upstream on-path switch reports the unexpected link. Misrouting is
    probabilistic, so this retries a few times. Returns the next free seq.
    """
    topo, routing = net.topo, net.routing
    for kind, link, _ in res.findings:
        if kind == "misroute" and link is not None:
            res.misconfigured_switch = link[0]
            return first_seq
    seq = first_seq
    for _ in range(attempts):
        r = net.probe(res.src, res.dst, seq, usm=True, authorized=True)
        seq += 1
        r.findings = diagnose(topo, routing, r)
        res.loop_phase2.append(r)
        for kind, link, _ in r.findings:
            if kind == "misroute" and link is not None:
                res.misconfigured_switch = link[0]
                return seq
    return seq
