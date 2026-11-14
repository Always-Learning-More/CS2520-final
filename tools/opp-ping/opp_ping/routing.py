"""Shortest-path routing with equal-cost multipath (the paper's routing policy).

Only switches forward packets; hosts are end points. For a destination host
``dst``, ``dist[n]`` is the number of links from ``n`` to ``dst``. A switch's
equal-cost ports toward ``dst`` are the ports whose neighbor is exactly one
hop closer. Under packet spraying every packet picks one of them at random.
"""

import collections


class Routing(object):
    def __init__(self, topo):
        self.topo = topo
        self._dist = {}
        self._ecmp = {}
        self._paths = {}

    # ------------------------------------------------------------ distances
    def dist_to(self, dst):
        """dist[n] = hops from node n to host dst (switch-only transit)."""
        if dst not in self._dist:
            topo = self.topo
            dist = [-1] * topo.num_nodes
            dist[dst] = 0
            queue = collections.deque([dst])
            while queue:
                u = queue.popleft()
                if u != dst and topo.is_host(u):
                    continue            # hosts do not forward
                for v in topo.adj[u]:
                    if dist[v] < 0:
                        dist[v] = dist[u] + 1
                        queue.append(v)
            self._dist[dst] = dist
        return self._dist[dst]

    def hops(self, src, dst):
        return self.dist_to(dst)[src]

    def switch_hops(self, src, dst):
        """Number of switches a packet traverses from host src to host dst."""
        return self.hops(src, dst) - 1

    # ---------------------------------------------------------------- ECMP
    def ecmp_ports(self, node, dst):
        """Equal-cost egress ports of ``node`` toward host ``dst`` (sorted)."""
        key = (node, dst)
        if key not in self._ecmp:
            dist = self.dist_to(dst)
            topo = self.topo
            want = dist[node] - 1
            ports = []
            for port, nbr in enumerate(topo.adj[node]):
                if dist[nbr] == want and (nbr == dst or not topo.is_host(nbr)):
                    ports.append(port)
            self._ecmp[key] = ports
        return self._ecmp[key]

    def on_dag(self, node, src, dst):
        """True if node lies on some shortest path from src to dst."""
        if self.topo.is_host(node):
            return node in (src, dst)
        return self.hops(src, node) + self.hops(node, dst) == self.hops(src, dst)

    def dag_switches(self, src, dst):
        """Switches on the equal-cost path DAG from src to dst, in hop order."""
        first = self.topo.attached_switch(src)
        order, seen, frontier = [], {first}, [first]
        while frontier:
            order.extend(sorted(frontier))
            nxt = []
            for s in frontier:
                for p in self.ecmp_ports(s, dst):
                    nb = self.topo.neighbor(s, p)
                    if nb != dst and nb not in seen:
                        seen.add(nb)
                        nxt.append(nb)
            frontier = nxt
        return order

    def path_count(self, src, dst):
        """Number of distinct equal-cost paths from host src to host dst."""
        key = (src, dst)
        if key not in self._paths:
            memo = {dst: 1}

            def count(n):
                if n in memo:
                    return memo[n]
                total = 0
                for p in self.ecmp_ports(n, dst):
                    total += count(self.topo.neighbor(n, p))
                memo[n] = total
                return total

            # iterative order (deepest first) to avoid recursion limits
            for s in reversed(self.dag_switches(src, dst)):
                count(s)
            self._paths[key] = count(self.topo.attached_switch(src))
        return self._paths[key]

    # ------------------------------------------- furthest / closest device
    def _host_distances(self, src):
        topo = self.topo
        dist = [-1] * topo.num_nodes
        dist[src] = 0
        queue = collections.deque([src])
        while queue:
            u = queue.popleft()
            if u != src and topo.is_host(u):
                continue
            for v in topo.adj[u]:
                if dist[v] < 0:
                    dist[v] = dist[u] + 1
                    queue.append(v)
        return dist

    def furthest_hosts(self, src):
        """All hosts at the maximum hop distance from src, and that distance."""
        dist = self._host_distances(src)
        best = max(dist[h] for h in self.topo.hosts if h != src)
        return [h for h in self.topo.hosts if h != src and dist[h] == best], best

    def closest_hosts(self, src):
        """All hosts at the minimum hop distance from src, and that distance."""
        dist = self._host_distances(src)
        best = min(dist[h] for h in self.topo.hosts if h != src and dist[h] > 0)
        return [h for h in self.topo.hosts if h != src and dist[h] == best], best

    def pick_furthest(self, src, rng):
        """Randomly choose one of the furthest hosts (random tie-breaker)."""
        candidates, distance = self.furthest_hosts(src)
        return rng.choice(candidates), candidates, distance

    def pick_target(self, src, target, rng):
        """target is "furthest" or "closest"; ties are broken at random."""
        if target == "furthest":
            candidates, distance = self.furthest_hosts(src)
        elif target == "closest":
            candidates, distance = self.closest_hosts(src)
        else:
            raise ValueError("target must be 'furthest' or 'closest'")
        return rng.choice(candidates), candidates, distance

    SYMMETRIC = ("fat-tree", "leaf-spine", "2d-torus", "3d-torus")

    def diameter_switch_hops(self):
        """Max switch hops between any two hosts. Host 0 is representative in
        the symmetric topologies; otherwise every host is checked."""
        sources = [0] if self.topo.arch in self.SYMMETRIC else list(self.topo.hosts)
        return max(self.furthest_hosts(h)[1] for h in sources) - 1

    # ------------------------------------------------------- route description
    def hop_layers(self, src, dst):
        """Switches that can appear at each switch hop from src to dst."""
        layers, frontier = [], [self.topo.attached_switch(src)]
        while frontier:
            layers.append(sorted(frontier))
            nxt = set()
            for s in frontier:
                for p in self.ecmp_ports(s, dst):
                    nb = self.topo.neighbor(s, p)
                    if nb != dst:
                        nxt.add(nb)
            frontier = sorted(nxt)
        return layers

    def _layer_text(self, layer):
        names = [self.topo.name[s] for s in layer]
        if len(names) == 1:
            return names[0]
        if len(names) <= 4:
            return "{" + "|".join(names) + "}"
        return "{%s .. %s: %d switches}" % (names[0], names[-1], len(names))

    def route_text(self, src, dst):
        """'11.0.0.1 host-0 -> tor-p0-0 -> {agg-p0-0|agg-p0-1} -> ... -> host-9 11.0.9.1'"""
        topo = self.topo
        middle = " -> ".join(self._layer_text(l) for l in self.hop_layers(src, dst))
        return "%s %s -> %s -> %s %s" % (topo.ip(src), topo.name[src], middle,
                                         topo.name[dst], topo.ip(dst))

    def traverse_text(self, src, dst):
        """One-line plain description of how to get from src to dst."""
        topo = self.topo
        layers = self.hop_layers(src, dst)
        hops = len(layers)
        if topo.arch in ("2d-torus", "3d-torus"):
            return "%d switch hops: %s" % (hops, self._torus_moves(src, dst))
        if hops == 1:
            return "1 switch hop: both hosts hang off %s, so traffic never leaves it" \
                   % topo.name[layers[0][0]]
        height = [self._height(l[0]) for l in layers]
        peak = max(range(hops), key=lambda i: (height[i], -i))
        if peak == 0:                     # the source switch is the highest point
            parts = ["down " + self._segment(layers, "down")]
        else:
            parts = ["up " + self._segment(layers[:peak + 1], "up")]
            if layers[peak + 1:]:
                parts.append("down " + self._segment(layers[peak + 1:], "down"))
        return "%d switch hops: %s" % (hops, "; ".join(parts))

    def _segment(self, layers, direction):
        """Describe consecutive layers; runs along one OT daisy chain are
        compressed to 'the cell-1a chain 1a-s8 .. 1a-s1 (8 switches)'."""
        topo = self.topo
        words, i = [], 0
        while i < len(layers):
            cell = topo.coord[layers[i][0]].get("cell") if len(layers[i]) == 1 else None
            j = i
            if cell is not None:
                while (j + 1 < len(layers) and len(layers[j + 1]) == 1
                       and topo.coord[layers[j + 1][0]].get("cell") == cell):
                    j += 1
            run = layers[i:j + 1]
            if cell is not None and len(run) >= 3:
                words.append("the cell-%s chain %s .. %s (%d switches)"
                             % (cell, topo.name[run[0][0]], topo.name[run[-1][0]], len(run)))
            elif cell is not None:
                words.append("the cell-%s chain: %s" % (cell, " -> ".join(topo.name[l[0]] for l in run)))
            else:
                words.extend(self._layer_word(l) for l in run)
            i = j + 1
        return " -> ".join(words)

    def _layer_word(self, layer):
        if len(layer) == 1:
            return self.topo.name[layer[0]]
        return "%s (any of %d)" % (self.topo.tier[layer[0]], len(layer))

    def _height(self, switch):
        """Position in the hierarchy (bigger = closer to the top)."""
        topo = self.topo
        if topo.arch == "ot-purdue":
            core = next(s for s in topo.switches if topo.tier[s] == "core")
            return -self.hops(switch, core) if switch != core else 0
        return {"tor": 1, "leaf": 1, "agg": 2, "spine": 2, "core": 3}.get(topo.tier[switch], 0)

    def _torus_moves(self, src, dst):
        topo = self.topo
        n = topo.params["n"]
        a, b = topo.coord[src], topo.coord[dst]
        moves = []
        for axis in ("x", "y", "z"):
            if axis not in a:
                continue
            d = (b[axis] - a[axis]) % n
            if d == 0:
                continue
            if 2 * d == n:
                moves.append("%s %d steps (either direction, wrap-around)" % (axis, d))
            elif 2 * d < n:
                moves.append("%s +%d" % (axis, d))
            else:
                moves.append("%s -%d (wrap-around)" % (axis, n - d))
        return ", ".join(moves) + (" in any order" if len(moves) > 1 else "")

    # -------------------------------------------------- redundancy analysis
    def cut_off_hosts(self, src, a, b):
        """Hosts that can no longer reach src if the link a-b goes down."""
        topo = self.topo
        seen = {src}
        queue = collections.deque([src])
        while queue:
            u = queue.popleft()
            if u != src and topo.is_host(u):
                continue
            for v in topo.adj[u]:
                if (u, v) in ((a, b), (b, a)) or v in seen:
                    continue
                seen.add(v)
                queue.append(v)
        return [h for h in topo.hosts if h not in seen]

    def bridges(self):
        """Links whose loss splits the network, with the number of hosts cut
        off on the smaller side: [((a, b), hosts_cut), ...]."""
        topo = self.topo
        total = topo.num_hosts
        disc = [-1] * topo.num_nodes
        low = [0] * topo.num_nodes
        sub_hosts = [0] * topo.num_nodes
        out = []
        timer = 0
        for root in range(topo.num_nodes):
            if disc[root] >= 0:
                continue
            disc[root] = low[root] = timer
            timer += 1
            sub_hosts[root] = 1 if topo.is_host(root) else 0
            stack = [(root, -1, iter(topo.adj[root]))]
            while stack:
                u, parent, it = stack[-1]
                advanced = False
                for v in it:
                    if v == parent:
                        continue
                    if disc[v] < 0:
                        disc[v] = low[v] = timer
                        timer += 1
                        sub_hosts[v] = 1 if topo.is_host(v) else 0
                        stack.append((v, u, iter(topo.adj[v])))
                        advanced = True
                        break
                    low[u] = min(low[u], disc[v])
                if advanced:
                    continue
                stack.pop()
                if parent >= 0:
                    low[parent] = min(low[parent], low[u])
                    sub_hosts[parent] += sub_hosts[u]
                    if low[u] > disc[parent]:
                        cut = min(sub_hosts[u], total - sub_hosts[u])
                        out.append(((parent, u), cut))
        return out
