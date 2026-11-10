"""Data-center topologies used in the OmniPath Ping paper.

The generators reproduce the node numbering and link order of the topology
files in NS3/OPP/experiment_reproduction/topologies/ exactly (hosts first, then
switches), so node ids, port numbers and IP addresses agree with the ns-3
simulator. The test-suite checks this against the repo files.

IP addresses follow the simulator's node_id_to_ip():
    ip = 11.(id // 256).(id % 256).1
"""

import pathlib

HOST = "host"
SWITCH = "switch"

LINK_RATE_GBPS = 400
LINK_DELAY_NS = 1000


def node_id_to_ip(node_id):
    """Same mapping as scratch/third.cc node_id_to_ip()."""
    return "11.%d.%d.1" % (node_id // 256, node_id % 256)


def ip_to_node_id(ip):
    parts = ip.strip().split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        raise ValueError("not an IPv4 address: %r" % ip)
    a, b, c, d = (int(p) for p in parts)
    if a != 11 or d != 1 or b > 255 or c > 255:
        raise ValueError("%s is not a simulator address (expected 11.X.Y.1)" % ip)
    return b * 256 + c


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


ARCHES = {
    "fat-tree": (fat_tree, "k", 4),
    "leaf-spine": (leaf_spine, "k", 4),
    "2d-torus": (torus_2d, "n", 4),
    "3d-torus": (torus_3d, "n", 3),
}

ARCH_TITLES = {
    "fat-tree": "Fat-tree",
    "leaf-spine": "Leaf-spine",
    "2d-torus": "2D torus",
    "3d-torus": "3D torus",
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


def build(arch, size=None):
    if arch not in ARCHES:
        raise ValueError("unknown architecture %r (choose from %s)" % (arch, ", ".join(ARCHES)))
    fn, _, default = ARCHES[arch]
    return fn(default if size is None else size)


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
