"""Failure models from the paper's evaluation (Table 1, Section 6.1).

Each failure sits on one directed link ``switch --port--> neighbor``:

  acl-drop    flow-level   ACL drop rule matching the probed service flow
              drops every packet of that flow on the link (probes mimic the
              flow, ACKs are the reverse flow and are not matched).
  loop        flow-level   routing misconfiguration: the *unicast* table of
              the switch sends packets that should leave on ``port`` to a
              wrong neighbor with probability ``loop_prob`` (paper: 50%). The
              native multicast table is correct, so only USM probes see it.
  link-flap   device-level the link drops ``flap_prob`` (paper: 20%) of all
              packets, in both directions.
  congestion  device-level ``congestion_us`` (paper: 500 us) of extra queueing
              delay on the link.
  port-down   device-level the link drops every packet in both directions.
  none        healthy network.
"""

FAILURE_INFO = {
    "none": ("-", "healthy network, no failure injected"),
    "acl-drop": ("flow-level", "ACL drop rule misconfiguration (Table 1 #8): drops the probed flow"),
    "loop": ("flow-level", "routing misconfiguration -> forwarding loop (Table 1 #9)"),
    "link-flap": ("device-level", "link flapping (Table 1 #1): random packet loss on the link"),
    "congestion": ("device-level", "congestion (Table 1 #4): abnormal queueing delay on the link"),
    "port-down": ("device-level", "port down (Table 1 #2): link drops everything"),
}

FAILURE_TYPES = tuple(FAILURE_INFO)

# Preferred switch tier for automatic placement, mirroring the paper's
# setup (failures on uplinks out of the source-side aggregation layer).
PREFERRED_TIER = {"fat-tree": "agg", "leaf-spine": "leaf"}


class Failure(object):
    def __init__(self, kind, topo, switch=None, port=None, wrong_port=None,
                 flap_prob=0.2, congestion_us=500.0, loop_prob=0.5):
        if kind not in FAILURE_INFO:
            raise ValueError("unknown failure %r (choose from %s)" % (kind, ", ".join(FAILURE_TYPES)))
        self.kind = kind
        self.topo = topo
        self.switch = switch
        self.port = port
        self.wrong_port = wrong_port
        self.flap_prob = flap_prob
        self.congestion_ns = int(congestion_us * 1000)
        self.loop_prob = loop_prob
        self.flow = None            # (src, dst) matched by ACL rules
        if kind != "none":
            self.neighbor = topo.neighbor(switch, port)
            self.reverse = (self.neighbor, topo.port_of(self.neighbor, switch))
        else:
            self.neighbor = None
            self.reverse = None

    # ------------------------------------------------------------- queries
    @property
    def category(self):
        return FAILURE_INFO[self.kind][0]

    @property
    def link(self):
        return None if self.kind == "none" else (self.switch, self.port)

    def link_text(self):
        if self.kind == "none":
            return "-"
        return "%s port %d -> %s" % (self.topo.name[self.switch], self.port,
                                     self.topo.name[self.neighbor])

    def describe(self):
        if self.kind == "none":
            return "none (healthy network)"
        detail = {
            "acl-drop": "drops all packets of flow %s" % self._flow_text(),
            "loop": "unicast lookups for port %d go to %s instead with p=%.0f%%"
                    % (self.port, self.topo.name[self.topo.neighbor(self.switch, self.wrong_port)]
                       if self.wrong_port is not None else "?", self.loop_prob * 100),
            "link-flap": "drops %.0f%% of packets in both directions" % (self.flap_prob * 100),
            "congestion": "+%.0f us queueing delay" % (self.congestion_ns / 1000.0),
            "port-down": "drops 100% of packets in both directions",
        }[self.kind]
        return "%s on %s: %s [%s]" % (self.kind, self.link_text(), detail, self.category)

    def _flow_text(self):
        if not self.flow:
            return "?"
        s, d = self.flow
        return "%s -> %s" % (self.topo.ip(s), self.topo.ip(d))

    # ------------------------------------------------------- data plane hooks
    def egress(self, node, port, is_probe, flow, rng):
        """Return (dropped, reason, extra_queue_delay_ns) for a packet leaving node:port."""
        k = self.kind
        if k == "none":
            return False, None, 0
        forward = (node == self.switch and port == self.port)
        backward = ((node, port) == self.reverse)
        if k == "congestion" and forward:
            return False, None, self.congestion_ns
        if k == "acl-drop" and forward and is_probe and flow == self.flow:
            return True, "ACL drop rule", 0
        if k == "port-down" and (forward or backward):
            return True, "port down", 0
        if k == "link-flap" and (forward or backward) and rng.random() < self.flap_prob:
            return True, "link flap", 0
        return False, None, 0

    def unicast_override(self, node, port, rng):
        """Unicast table lookup result after a possible routing misconfiguration."""
        if (self.kind == "loop" and node == self.switch and port == self.port
                and self.wrong_port is not None and rng.random() < self.loop_prob):
            return self.wrong_port
        return port


def place(kind, topo, routing, src, dst, rng, fail_at=None, fail_link=None, **params):
    """Create a Failure on (or near) the src->dst equal-cost paths.

    fail_at   "DEVICE" or "DEVICE:PORT" (device = name, id or IP)
    fail_link "A,B"  -> the link from A toward B
    Without either, a switch on the flow's path DAG is chosen like the paper
    does (source-side aggregation uplinks), with random tie-breaking.
    """
    if kind == "none":
        return Failure("none", topo)

    if fail_link:
        a_txt, b_txt = [x.strip() for x in fail_link.split(",", 1)]
        a, b = topo.lookup(a_txt), topo.lookup(b_txt)
        if b not in topo.adj[a]:
            raise ValueError("%s and %s are not directly connected" % (topo.name[a], topo.name[b]))
        switch, port = a, topo.port_of(a, b)
    elif fail_at:
        dev, _, port_txt = fail_at.partition(":")
        switch = topo.lookup(dev)
        if port_txt:
            port = int(port_txt)
            if not 0 <= port < len(topo.adj[switch]):
                raise ValueError("%s has ports 0..%d" % (topo.name[switch], len(topo.adj[switch]) - 1))
        else:
            ports = routing.ecmp_ports(switch, dst) or list(range(len(topo.adj[switch])))
            port = rng.choice(ports)
    else:
        switch, port = _auto_location(topo, routing, src, dst, rng,
                                      need_wrong_port=(kind == "loop"))

    if topo.is_host(switch):
        raise ValueError("failures must be placed on a switch, not %s" % topo.name[switch])

    wrong_port = None
    if kind == "loop":
        wrong_port = _wrong_port(topo, routing, switch, dst, rng)
        if wrong_port is None:
            raise ValueError("%s has no off-path neighbor to misroute to" % topo.name[switch])

    f = Failure(kind, topo, switch, port, wrong_port, **params)
    f.flow = (src, dst)
    return f


def _auto_location(topo, routing, src, dst, rng, need_wrong_port=False):
    dag = routing.dag_switches(src, dst)
    dst_tor = topo.attached_switch(dst)
    candidates = [s for s in dag if s != dst_tor]
    if need_wrong_port:
        # a misrouting failure needs an off-path switch neighbor to send to
        # (fat-tree: Agg -> ToR as in the paper; leaf-spine: spine -> leaf)
        with_wrong = [s for s in candidates if _has_wrong_port(topo, routing, s, dst)]
        candidates = with_wrong or candidates
    tier = PREFERRED_TIER.get(topo.arch)
    if tier:
        hops = routing.hops(src, dst)
        # upstream switches of the preferred tier (source side of the path)
        upstream = [s for s in candidates
                    if topo.tier[s] == tier and 2 * routing.hops(src, s) < hops]
        if upstream and (not need_wrong_port or all(
                _has_wrong_port(topo, routing, s, dst) for s in upstream)):
            candidates = upstream
    else:
        multi = [s for s in candidates if len(routing.ecmp_ports(s, dst)) > 1]
        if multi:
            candidates = multi
    switch = rng.choice(sorted(candidates))
    port = rng.choice(routing.ecmp_ports(switch, dst))
    return switch, port


def _has_wrong_port(topo, routing, switch, dst):
    good = set(routing.ecmp_ports(switch, dst))
    return any(p not in good and not topo.is_host(nb) for p, nb in enumerate(topo.adj[switch]))


def _wrong_port(topo, routing, switch, dst, rng):
    """A switch neighbor that is NOT one hop closer to dst (misrouting target)."""
    good = set(routing.ecmp_ports(switch, dst))
    bad = [p for p, nb in enumerate(topo.adj[switch])
           if p not in good and not topo.is_host(nb)]
    return rng.choice(bad) if bad else None
