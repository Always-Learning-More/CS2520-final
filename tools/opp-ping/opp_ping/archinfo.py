"""General information about each architecture, including OPP's resource
formulas from Section 5.2 / Appendix D of the paper."""

from . import engine
from .topology import node_id_to_ip, ARCH_TITLES

ENTRY_BYTES = 42                 # OP cache entry size on Tofino (Appendix F.1)
DEFAULT_M = {"m": 1, "m1": 10, "m2": 10, "m3": 1}   # paper's simulation settings

PAPER_SCALE = {
    "fat-tree": "paper evaluates k=32 (8,192 GPUs) and k=64 (65,536 GPUs)",
    "leaf-spine": "paper evaluates k=32 and k=64 (2,048 hosts)",
    "2d-torus": "paper evaluates 4x4 .. 10x10 (100 hosts)",
    "3d-torus": "paper evaluates 3x3x3 .. 6x6x6 (216 hosts)",
    "ot-purdue": "not in the paper: Purdue-model plant network (levels 3 -> 2 -> 1/0); "
                 "chains of 12 give the minimum 52 hosts / 51 switches",
    "dragonfly": "paper Table 6 only (grouping heuristic): 129 groups x 16 switches, "
                 "8 global links/switch, 16,512 hosts; build it with --size paper",
    "dragonfly-plus": "paper Table 6 only (grouping heuristic): 33 groups x (32 leaf + 32 spine), "
                      "67,584 hosts; build it with --size paper",
}

# Source host used by the demo and the OT tests (closest/furthest are measured
# from here). The OT network starts at 11.0.11.1, which with chains of 12 is
# dev-1a-8, low in cell 1a's daisy chain.
DEFAULT_SOURCE = {"ot-purdue": 11}


def default_source(topo):
    return DEFAULT_SOURCE.get(topo.arch, 0)

GROUPING = {
    "fat-tree": "task group = (source ToR, destination PoD); "
                "Token_ID = <src ToR, dst PoD, token>; core switches keep no OP cache",
    "leaf-spine": "task group = (source ToR, destination ToR); "
                  "Token_ID = <src ToR, dst ToR, token>; spines keep no OP cache",
    "2d-torus": "task group = source node; Token_ID = <src node, token>",
    "3d-torus": "task group = source node; Token_ID = <src node, token>",
    "ot-purdue": "task group = source switch; Token_ID = <src switch, token> "
                 "(no equal-cost paths, so probes are never duplicated)",
    "dragonfly": "no topology-specialized grouping in the paper; Section 5.3's merge "
                 "heuristic groups tasks (solver/unit-merge, example 'dragonfly')",
    "dragonfly-plus": "no topology-specialized grouping in the paper; Section 5.3's merge "
                      "heuristic groups tasks (solver/unit-merge, example 'dragonfly-plus')",
}

# Paper Table 6, heuristic column, at the paper's sizes (B = 20).
TABLE6_ENTRIES = {"dragonfly": 4096, "dragonfly-plus": 4096}


def op_cache(arch, size, m=None):
    """(entries per switch, concurrent in-flight probes per source ToR, formula text)."""
    p = dict(DEFAULT_M)
    p.update(m or {})
    if arch == "fat-tree":
        k = size
        entries = p["m1"] + p["m2"] * (k // 2) + p["m3"] * k * k
        conc = p["m1"] + p["m2"] + p["m3"] * (k - 1)
        text = "m1 + m2*k/2 + m3*k^2 with m1=%(m1)d m2=%(m2)d m3=%(m3)d" % p
    elif arch == "leaf-spine":
        k = size                      # number of leaf (ToR) switches
        entries = p["m1"] + 2 * p["m2"] * k
        conc = p["m1"] + p["m2"] * (k - 1)
        text = "m1 + 2*m2*k with m1=%(m1)d m2=%(m2)d" % p
    elif arch in ("dragonfly", "dragonfly-plus"):
        return None, None, ("no closed-form formula in the paper; Table 6 reports %d entries "
                            "per switch from the merge heuristic at paper scale. Run "
                            "'bash scripts/reproduce_six_tor_scope.sh %s' in solver/unit-merge"
                            % (TABLE6_ENTRIES[arch], arch))
    elif arch == "ot-purdue":
        d = 3 + 4 * size              # every switch is a source switch for its hosts
        entries = p["m"] * d
        conc = p["m"]
        text = "m*d with m=%d, d=%d switches" % (p["m"], d)
    else:
        d = size ** (2 if arch == "2d-torus" else 3)
        entries = p["m"] * d
        conc = p["m"]
        text = "m*d with m=%d, d=%d nodes" % (p["m"], d)
    return entries, conc, text


def _hops(n):
    return "1 switch hop" if n == 1 else "%d switch hops" % n


def summarize(topo, routing):
    arch, size = topo.arch, list(topo.params.values())[0]
    counts = topo.tier_counts()
    host_links = topo.num_hosts
    fabric_links = len(topo.links) - host_links
    src = default_source(topo)
    furthest, dist = routing.furthest_hosts(src)
    closest, cdist = routing.closest_hosts(src)
    far, near = furthest[0], closest[0]
    paths = routing.path_count(src, far)
    rtt = 2 * dist * (engine.LINK_DELAY_NS + engine.TX_NS) / 1000.0
    radix = max(len(topo.adj[s]) for s in topo.switches)
    entries, conc, formula = op_cache(arch, size)
    tiers = ", ".join("%s %d" % (t, counts[t])
                      for t in ("tor", "agg", "core", "leaf", "spine", "area", "cell", "node",
                                "router")
                      if t in counts)
    if "groups" in topo.params:
        tiers += " in %d groups" % topo.params["groups"]
    bridges = routing.bridges()
    fabric_bridges = [(l, c) for l, c in bridges
                      if not (topo.is_host(l[0]) or topo.is_host(l[1]))]
    worst = max(bridges, key=lambda b: b[1]) if bridges else None

    def ips(hosts):
        shown = ", ".join(topo.ip(h) for h in hosts[:4])
        return shown + (" ... (%d total)" % len(hosts) if len(hosts) > 4 else "")

    info = [
        ("architecture", topo.describe()),
        ("hosts", "%d  (IPs %s .. %s)" % (topo.num_hosts, node_id_to_ip(0), node_id_to_ip(topo.num_hosts - 1))),
        ("switches", "%d  (%s), max radix %d" % (topo.num_nodes - topo.num_hosts, tiers, radix)),
        ("links", "%d  (%d host links, %d fabric links), %d Gbps, %d us each"
         % (len(topo.links), host_links, fabric_links, 400, engine.LINK_DELAY_NS // 1000)),
        ("diameter", "%d switch hops between the furthest hosts" % routing.diameter_switch_hops()),
        ("source for tests", "%s %s" % (topo.ip(src), topo.name[src])),
        ("furthest", "%d host(s) at %s: %s" % (len(furthest), _hops(dist - 1), ips(furthest))),
        ("  route", routing.route_text(src, far)),
        ("  traverse", routing.traverse_text(src, far)),
        ("closest", "%d host(s) at %s: %s" % (len(closest), _hops(cdist - 1), ips(closest))),
        ("  route", routing.route_text(src, near)),
        ("  traverse", routing.traverse_text(src, near)),
        ("equal-cost paths", "%d to the furthest host, %d to the closest"
         % (paths, routing.path_count(src, near))),
        ("single points of failure", "%d of %d fabric links (plus %d host access links)%s"
         % (len(fabric_bridges), fabric_links, host_links,
            "; worst single-link outage %s cuts off %d hosts"
            % ("%s - %s" % (topo.name[worst[0][0]], topo.name[worst[0][1]]), worst[1])
            if worst and worst[1] > 1 else "; no fabric link outage isolates any host")),
        ("max base RTT", "%.1f us (furthest pair)" % rtt),
        ("OPP task grouping", GROUPING[arch]),
        ("OP cache / switch", formula if entries is None else
         "%d entries = %.1f KB at %d B/entry  [%s]"
         % (entries, entries * ENTRY_BYTES / 1024.0, ENTRY_BYTES, formula)),
        ("in-flight probes", "set by the heuristic's task groups" if conc is None
         else "%d per source ToR" % conc),
        ("paper scale", PAPER_SCALE[arch]),
    ]
    return info


def title(topo):
    return ARCH_TITLES.get(topo.arch, topo.arch)
