"""Data-center topologies used in the OmniPath Ping paper.

The generators reproduce the node numbering and link order of the topology
files in NS3/OPP/experiment_reproduction/topologies/ exactly (hosts first, then
switches), so node ids, port numbers and IP addresses agree with the ns-3
simulator. The test-suite checks this against the repo files.

IP addresses follow the simulator's node_id_to_ip():
    ip = 11.(id // 256).(id % 256).1, rolling over into 12.X.Y.1 past id 65,535
"""

import pathlib

HOST = "host"
SWITCH = "switch"

LINK_RATE_GBPS = 400
LINK_DELAY_NS = 1000


def node_id_to_ip(node_id):
    """Same 32-bit arithmetic as scratch/third.cc node_id_to_ip():
    0x0b000001 + (id / 256) * 0x10000 + (id % 256) * 0x100.
    Ids up to 65,535 give 11.X.Y.1; larger ids roll over into 12.X.Y.1 etc."""
    v = 0x0b000001 + (node_id // 256) * 0x10000 + (node_id % 256) * 0x100
    return "%d.%d.%d.%d" % (v >> 24 & 0xff, v >> 16 & 0xff, v >> 8 & 0xff, v & 0xff)


def ip_to_node_id(ip):
    parts = ip.strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        raise ValueError("not an IPv4 address: %r" % ip)
    a, b, c, d = (int(p) for p in parts)
    if a < 11 or d != 1 or b > 255 or c > 255 or a > 255:
        raise ValueError("%s is not a simulator address (expected 11.X.Y.1)" % ip)
    v = (a << 24 | b << 16 | c << 8 | d) - 0x0b000001
    return (v >> 16) * 256 + (v >> 8 & 0xff)


class Topology(object):
    """Undirected graph with ordered ports per node.

    Port numbers are assigned in link order, exactly like ns-3 assigns
    interfaces while reading the topology file (port 0 is loopback in ns-3,
    so ns-3 interface index = port + 1).
    """

    def __init__(self, arch, params, num_hosts):
        self.arch = arch
        self.params = dict(params)
        self.num_hosts = num_hosts
        self.kind = []          # HOST / SWITCH per node id
        self.tier = []          # "host", "tor", "agg", "core", "leaf", "spine", "node"
        self.name = []
        self.coord = []         # topology-specific coordinates (pod, index, x/y/z ...)
        self.adj = []           # adj[node] = [neighbor, ...] in port order
        self.links = []         # (a, b) in file order
        self._port_of = []      # per node: {neighbor: port}

    # ----------------------------------------------------------- building
    def add_node(self, kind, tier, name, coord=None):
        self.kind.append(kind)
        self.tier.append(tier)
        self.name.append(name)
        self.coord.append(coord or {})
        self.adj.append([])
        self._port_of.append({})
        return len(self.kind) - 1

    def add_link(self, a, b):
        if b in self._port_of[a]:
            raise ValueError("duplicate link %d-%d" % (a, b))
        self._port_of[a][b] = len(self.adj[a])
        self.adj[a].append(b)
        self._port_of[b][a] = len(self.adj[b])
        self.adj[b].append(a)
        self.links.append((a, b))

    # ----------------------------------------------------------- queries
    @property
    def num_nodes(self):
        return len(self.kind)

    @property
    def hosts(self):
        return range(self.num_hosts)

    @property
    def switches(self):
        return range(self.num_hosts, self.num_nodes)

    def is_host(self, n):
        return self.kind[n] == HOST

    def port_of(self, node, neighbor):
        return self._port_of[node][neighbor]

    def neighbor(self, node, port):
        return self.adj[node][port]

    def ip(self, node):
        return node_id_to_ip(node)

    def lookup(self, token):
        """Resolve a node from an id, an IP address or a device name."""
        token = str(token).strip()
        if token.isdigit():
            n = int(token)
        elif token.count(".") == 3:
            n = ip_to_node_id(token)
        else:
            if not hasattr(self, "_by_name"):
                self._by_name = {nm: i for i, nm in enumerate(self.name)}
            if token not in self._by_name:
                raise ValueError("unknown device %r" % token)
            n = self._by_name[token]
        if not 0 <= n < self.num_nodes:
            raise ValueError("node %d does not exist in %s" % (n, self.describe()))
        return n

    def attached_switch(self, host):
        return self.adj[host][0]

    def link_name(self, node, port):
        return "%s -> %s" % (self.name[node], self.name[self.neighbor(node, port)])

    def tier_counts(self):
        counts = {}
        for t in self.tier:
            counts[t] = counts.get(t, 0) + 1
        return counts

    def describe(self):
        p = ", ".join("%s=%s" % kv for kv in sorted(self.params.items()))
        return "%s (%s)" % (ARCH_TITLES.get(self.arch, self.arch), p)

    def to_file_text(self):
        """Serialize in the ns-3 topology file format."""
        lines = ["%d %d %d" % (self.num_nodes, self.num_nodes - self.num_hosts, len(self.links)),
                 " ".join(str(s) for s in self.switches)]
        for a, b in self.links:
            lines.append("%d %d %dGbps %dns 0.000000" % (a, b, LINK_RATE_GBPS, LINK_DELAY_NS))
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------- generators
def fat_tree(k):
    """3-tier k-ary fat-tree: k pods, k/2 ToR + k/2 Agg per pod, (k/2)^2 cores."""
    if k < 2 or k % 2:
        raise ValueError("fat-tree k must be an even number >= 2")
    half = k // 2
    hosts = k * k * k // 4
    tors = aggs = k * half
    cores = half * half
    t = Topology("fat-tree", {"k": k}, hosts)
    for h in range(hosts):
        pod, rest = divmod(h, half * half)
        t.add_node(HOST, "host", "host-%d" % h, {"pod": pod, "tor": h // half})
    tor_base = hosts
    for i in range(tors):
        t.add_node(SWITCH, "tor", "tor-p%d-%d" % (i // half, i % half), {"pod": i // half, "index": i % half})
    agg_base = tor_base + tors
    for i in range(aggs):
        t.add_node(SWITCH, "agg", "agg-p%d-%d" % (i // half, i % half), {"pod": i // half, "index": i % half})
    core_base = agg_base + aggs
    for i in range(cores):
        t.add_node(SWITCH, "core", "core-%d" % i, {"group": i // half, "index": i})
    for h in range(hosts):
        t.add_link(h, tor_base + h // half)
    for i in range(tors):
        pod = i // half
        for j in range(half):
            t.add_link(tor_base + i, agg_base + pod * half + j)
    for i in range(aggs):
        j = i % half
        for c in range(half):
            t.add_link(agg_base + i, core_base + j * half + c)
    return t


def leaf_spine(k):
    """Leaf-spine built from radix-k switches: k leaves (k/2 hosts each), k/2 spines."""
    if k < 2 or k % 2:
        raise ValueError("leaf-spine k must be an even number >= 2")
    half = k // 2
    hosts = k * half
    t = Topology("leaf-spine", {"k": k}, hosts)
    for h in range(hosts):
        t.add_node(HOST, "host", "host-%d" % h, {"leaf": h // half})
    leaf_base = hosts
    for i in range(k):
        t.add_node(SWITCH, "leaf", "leaf-%d" % i, {"index": i})
    spine_base = leaf_base + k
    for i in range(half):
        t.add_node(SWITCH, "spine", "spine-%d" % i, {"index": i})
    for h in range(hosts):
        t.add_link(h, leaf_base + h // half)
    for i in range(k):
        for j in range(half):
            t.add_link(leaf_base + i, spine_base + j)
    return t


def torus_2d(n):
    """n x n 2D torus; every node is a switch with one host attached."""
    if n < 3:
        raise ValueError("2D torus size must be >= 3")
    d = n * n
    t = Topology("2d-torus", {"n": n}, d)
    for h in range(d):
        t.add_node(HOST, "host", "host-%d" % h, {"x": h % n, "y": h // n})
    for s in range(d):
        t.add_node(SWITCH, "node", "sw-%d-%d" % (s % n, s // n), {"x": s % n, "y": s // n})
    base = d
    for h in range(d):
        t.add_link(h, base + h)
    for r in range(n):                      # row (x) rings
        for c in range(n):
            t.add_link(base + r * n + c, base + r * n + (c + 1) % n)
    for r in range(n):                      # column (y) rings
        for c in range(n):
            t.add_link(base + r * n + c, base + ((r + 1) % n) * n + c)
    return t


def torus_3d(n):
    """n x n x n 3D torus; every node is a switch with one host attached."""
    if n < 3:
        raise ValueError("3D torus size must be >= 3")
    d = n ** 3
    t = Topology("3d-torus", {"n": n}, d)

    def xyz(i):
        return i % n, (i // n) % n, i // (n * n)

    for h in range(d):
        x, y, z = xyz(h)
        t.add_node(HOST, "host", "host-%d" % h, {"x": x, "y": y, "z": z})
    for s in range(d):
        x, y, z = xyz(s)
        t.add_node(SWITCH, "node", "sw-%d-%d-%d" % (x, y, z), {"x": x, "y": y, "z": z})
    base = d
    for h in range(d):
        t.add_link(h, base + h)
    for i in range(d):
        x, y, z = xyz(i)
        t.add_link(base + i, base + ((x + 1) % n) + y * n + z * n * n)
        t.add_link(base + i, base + x + ((y + 1) % n) * n + z * n * n)
        t.add_link(base + i, base + x + y * n + ((z + 1) % n) * n * n)
    return t


def ot_purdue(n):
    """Purdue-model OT plant network: core -> 2 areas -> 2 cells each, every
    cell a daisy chain of n switches with one field device per switch.

        Level 3   core            historian, eng-ws
        Level 2   area-1, area-2  hmi-1, hmi-2
        Level 1/0 cells 1a 1b 2a 2b: <cell>-s1 - <cell>-s2 - ... - <cell>-sn,
                  one device dev-<cell>-<i> on each chain switch

    Every switch has exactly one uplink: the graph is a tree, so there is a
    single path between any two hosts and every link is a single point of
    failure. Hosts: 4 + 4n, switches: 3 + 4n, links: 6 + 8n.
    """
    if n < 1:
        raise ValueError("ot-purdue chain length must be >= 1")
    cells = ["1a", "1b", "2a", "2b"]
    hosts = 4 + 4 * n
    t = Topology("ot-purdue", {"n": n}, hosts)
    t.add_node(HOST, "host", "historian", {"level": 3, "role": "historian"})
    t.add_node(HOST, "host", "eng-ws", {"level": 3, "role": "engineering workstation"})
    t.add_node(HOST, "host", "hmi-1", {"level": 2, "area": 1, "role": "HMI"})
    t.add_node(HOST, "host", "hmi-2", {"level": 2, "area": 2, "role": "HMI"})
    for cell in cells:
        for i in range(1, n + 1):
            t.add_node(HOST, "host", "dev-%s-%d" % (cell, i),
                       {"level": 0, "area": int(cell[0]), "cell": cell, "position": i,
                        "role": "field device"})
    core = t.add_node(SWITCH, "core", "core", {"level": 3})
    areas = [t.add_node(SWITCH, "area", "area-%d" % a, {"level": 2, "area": a}) for a in (1, 2)]
    chain = {}
    for cell in cells:
        chain[cell] = [t.add_node(SWITCH, "cell", "%s-s%d" % (cell, i),
                                  {"level": 1, "area": int(cell[0]), "cell": cell, "position": i})
                       for i in range(1, n + 1)]
    # host access links, in host order
    t.add_link(0, core)
    t.add_link(1, core)
    t.add_link(2, areas[0])
    t.add_link(3, areas[1])
    h = 4
    for cell in cells:
        for sw in chain[cell]:
            t.add_link(h, sw)
            h += 1
    # backbone: core -> areas -> first switch of each cell -> down each chain
    for a in areas:
        t.add_link(core, a)
    for cell in cells:
        t.add_link(areas[int(cell[0]) - 1], chain[cell][0])
    for cell in cells:
        for up, down in zip(chain[cell], chain[cell][1:]):
            t.add_link(up, down)
    return t


def _global_links(t, groups, routers, h):
    """Wire one global link between every pair of groups (consecutive
    arrangement): in group i, global link l = 0..a*h-1 leaves router l // h and
    goes to group (i + l + 1) mod g. ``routers[i]`` lists group i's routers."""
    g = len(groups)
    for i in range(g):
        for j in range(i + 1, g):
            d = j - i
            a_side = routers[i][(d - 1) // h]
            b_side = routers[j][(g - d - 1) // h]
            t.add_link(a_side, b_side)


def dragonfly(a, h=None, p=None):
    """Dragonfly: g = a*h + 1 groups of ``a`` switches. Switches in a group are
    wired all-to-all (local links); every switch has ``h`` global links and
    ``p`` hosts; each pair of groups shares exactly one global link.

    --size a uses h = a - 1 and p = a / 2 (a=4: 13 groups, 52 switches, 104
    hosts). The paper's Table 6 configuration is a=16, h=8, p=8
    (129 groups, 2,064 switches, 16,512 hosts): use size "paper".
    """
    if h is None:
        h = a - 1
    if p is None:
        p = a // 2
    if a < 2 or h < 1 or p < 1:
        raise ValueError("dragonfly needs a >= 2 switches per group, h >= 1, p >= 1")
    g = a * h + 1
    t = Topology("dragonfly", {"a": a, "h": h, "p": p, "groups": g}, g * a * p)
    for grp in range(g):
        for sw in range(a):
            for i in range(p):
                n = len(t.kind)
                t.add_node(HOST, "host", "host-%d" % n, {"group": grp, "switch": sw})
    routers = []
    for grp in range(g):
        routers.append([t.add_node(SWITCH, "router", "df-g%d-s%d" % (grp, sw),
                                   {"group": grp, "index": sw}) for sw in range(a)])
    for grp in range(g):
        for sw in range(a):
            for i in range(p):
                t.add_link((grp * a + sw) * p + i, routers[grp][sw])
    for grp in range(g):                       # local all-to-all inside each group
        for x in range(a):
            for y in range(x + 1, a):
                t.add_link(routers[grp][x], routers[grp][y])
    _global_links(t, range(g), routers, h)
    return t


def dragonfly_plus(s, h=None, p=None, leaves=None):
    """Dragonfly+: g = s*h + 1 groups; each group is a leaf-spine with
    ``leaves`` leaves and ``s`` spines (full bipartite). Hosts hang off the
    leaves (``p`` each); every spine has ``h`` global links and each pair of
    groups shares exactly one global link.

    --size s uses leaves = s, h = s / 2 and p = 2 (s=4: 9 groups, 72 switches,
    72 hosts). The paper's Table 6 configuration is 33 groups of 32 leaves +
    32 spines with h=1 and 64 hosts per leaf (2,112 switches, 67,584 hosts):
    use size "paper".
    """
    if leaves is None:
        leaves = s
    if h is None:
        h = max(1, s // 2)
    if p is None:
        p = 2
    if s < 2 or leaves < 1 or h < 1 or p < 1:
        raise ValueError("dragonfly-plus needs s >= 2 spines per group, h >= 1, p >= 1")
    g = s * h + 1
    t = Topology("dragonfly-plus", {"s": s, "leaves": leaves, "h": h, "p": p, "groups": g},
                 g * leaves * p)
    for grp in range(g):
        for lf in range(leaves):
            for i in range(p):
                n = len(t.kind)
                t.add_node(HOST, "host", "host-%d" % n, {"group": grp, "leaf": lf})
    leaf_ids, spine_ids = [], []
    for grp in range(g):
        leaf_ids.append([t.add_node(SWITCH, "leaf", "leaf-g%d-%d" % (grp, i),
                                    {"group": grp, "index": i}) for i in range(leaves)])
    for grp in range(g):
        spine_ids.append([t.add_node(SWITCH, "spine", "spine-g%d-%d" % (grp, i),
                                     {"group": grp, "index": i}) for i in range(s)])
    for grp in range(g):
        for lf in range(leaves):
            for i in range(p):
                t.add_link((grp * leaves + lf) * p + i, leaf_ids[grp][lf])
    for grp in range(g):
        for lf in leaf_ids[grp]:
            for sp in spine_ids[grp]:
                t.add_link(lf, sp)
    _global_links(t, range(g), spine_ids, h)
    return t


# Every model must have at least this many hosts AND switches.
MIN_HOSTS = 50
MIN_SWITCHES = 50

# arch -> (generator, size parameter name, default size)
# Defaults are the smallest sizes meeting the 50 + 50 floor.
ARCHES = {
    "fat-tree": (fat_tree, "k", 8),          # 128 hosts,  80 switches
    "leaf-spine": (leaf_spine, "k", 34),     # 578 hosts,  51 switches
    "2d-torus": (torus_2d, "n", 8),          #  64 hosts,  64 switches
    "3d-torus": (torus_3d, "n", 4),          #  64 hosts,  64 switches
    "ot-purdue": (ot_purdue, "n", 12),       #  52 hosts,  51 switches
    "dragonfly": (dragonfly, "a", 4),        # 104 hosts,  52 switches (13 groups)
    "dragonfly-plus": (dragonfly_plus, "s", 4),  # 72 hosts, 72 switches (9 groups)
}

# size="paper" builds the configuration the paper evaluates.
PAPER_SIZES = {
    "fat-tree": lambda: fat_tree(64),
    "leaf-spine": lambda: leaf_spine(64),
    "2d-torus": lambda: torus_2d(10),
    "3d-torus": lambda: torus_3d(6),
    "dragonfly": lambda: dragonfly(16, h=8, p=8),
    "dragonfly-plus": lambda: dragonfly_plus(32, h=1, p=64, leaves=32),
}

# (hosts, switches) as a function of size, and whether a size is valid
COUNTS = {
    "fat-tree": lambda k: (k ** 3 // 4, 5 * k * k // 4),
    "leaf-spine": lambda k: (k * k // 2, 3 * k // 2),
    "2d-torus": lambda n: (n * n, n * n),
    "3d-torus": lambda n: (n ** 3, n ** 3),
    "ot-purdue": lambda n: (4 + 4 * n, 3 + 4 * n),
    "dragonfly": lambda a: ((a * (a - 1) + 1) * a * (a // 2), (a * (a - 1) + 1) * a),
    "dragonfly-plus": lambda s: ((s * max(1, s // 2) + 1) * s * 2,
                                 (s * max(1, s // 2) + 1) * 2 * s),
}
VALID_SIZE = {
    "fat-tree": lambda k: k >= 2 and k % 2 == 0,
    "leaf-spine": lambda k: k >= 2 and k % 2 == 0,
    "2d-torus": lambda n: n >= 3,
    "3d-torus": lambda n: n >= 3,
    "ot-purdue": lambda n: n >= 1,
    "dragonfly": lambda a: a >= 2 and a % 2 == 0,
    "dragonfly-plus": lambda s: s >= 2 and s % 2 == 0,
}

ARCH_TITLES = {
    "fat-tree": "Fat-tree",
    "leaf-spine": "Leaf-spine",
    "2d-torus": "2D torus",
    "3d-torus": "3D torus",
    "ot-purdue": "OT Purdue plant",
    "dragonfly": "Dragonfly",
    "dragonfly-plus": "Dragonfly+",
}

# Topology files shipped in the repo, keyed by (arch, size).
REPO_TOPOLOGY_FILES = {
    ("fat-tree", 32): "fattree/fattree_k32_topology.txt",
    ("fat-tree", 64): "fattree/fattree_k64_topology.txt",
    ("leaf-spine", 32): "leaf_spine/leaf_spine_k32_topology.txt",
    ("leaf-spine", 64): "leaf_spine/leaf_spine_k64_topology.txt",
    ("2d-torus", 4): "2d_torus/torus_4x4_topology.txt",
    ("2d-torus", 6): "2d_torus/torus_6x6_topology.txt",
    ("2d-torus", 8): "2d_torus/torus_8x8_topology.txt",
    ("2d-torus", 10): "2d_torus/torus_10x10_topology.txt",
    ("3d-torus", 3): "3d_torus/torus_3x3x3_topology.txt",
    ("3d-torus", 4): "3d_torus/torus_4x4x4_topology.txt",
    ("3d-torus", 5): "3d_torus/torus_5x5x5_topology.txt",
    ("3d-torus", 6): "3d_torus/torus_6x6x6_topology.txt",
}


def minimum_size(arch):
    """Smallest valid size with at least MIN_HOSTS hosts and MIN_SWITCHES switches."""
    size = 1
    while True:
        if VALID_SIZE[arch](size):
            hosts, switches = COUNTS[arch](size)
            if hosts >= MIN_HOSTS and switches >= MIN_SWITCHES:
                return size
        size += 1


def generate(arch, size=None):
    """Build any size, without the 50-host / 50-switch floor (used for unit tests
    of small shapes and for comparing against the repo's smaller topology files).
    size may be an int, None (default) or "paper"."""
    if arch not in ARCHES:
        raise ValueError("unknown architecture %r (choose from %s)" % (arch, ", ".join(ARCHES)))
    if size == "paper":
        if arch not in PAPER_SIZES:
            raise ValueError("%s is not in the paper; it has no paper size" % arch)
        return PAPER_SIZES[arch]()
    fn, _, default = ARCHES[arch]
    return fn(default if size is None else size)


def build(arch, size=None):
    """Build a model; refuses sizes below 50 hosts or 50 switches."""
    if arch not in ARCHES:
        raise ValueError("unknown architecture %r (choose from %s)" % (arch, ", ".join(ARCHES)))
    if size == "paper":
        return generate(arch, "paper")
    _, param, default = ARCHES[arch]
    size = default if size is None else size
    if not VALID_SIZE[arch](size):
        raise ValueError("%s %s=%d is not a valid size" % (arch, param, size))
    hosts, switches = COUNTS[arch](size)
    if hosts < MIN_HOSTS or switches < MIN_SWITCHES:
        raise ValueError("%s %s=%d has %d hosts and %d switches; every model needs at least "
                         "%d hosts and %d switches (smallest allowed: %s=%d)"
                         % (arch, param, size, hosts, switches, MIN_HOSTS, MIN_SWITCHES,
                            param, minimum_size(arch)))
    return generate(arch, size)


def repo_topology_dir():
    """Locate NS3/OPP/experiment_reproduction/topologies relative to this file."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "NS3" / "OPP" / "experiment_reproduction" / "topologies"
        if cand.is_dir():
            return cand
    return None


def read_topology_file(path):
    """Parse an ns-3 topology file into (num_nodes, switch_ids, links)."""
    with open(str(path)) as f:
        header = f.readline().split()
        num_nodes, num_switches, num_links = (int(x) for x in header[:3])
        switches = [int(x) for x in f.readline().split()]
        links = []
        for line in f:
            parts = line.split()
            if len(parts) >= 2:
                links.append((int(parts[0]), int(parts[1])))
    if len(switches) != num_switches or len(links) != num_links:
        raise ValueError("%s: header does not match contents" % path)
    return num_nodes, switches, links
