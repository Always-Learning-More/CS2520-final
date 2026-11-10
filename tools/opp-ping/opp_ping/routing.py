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

    # ------------------------------------------------------ furthest device
    def furthest_hosts(self, src):
        """All hosts at the maximum hop distance from src, and that distance."""
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
        best = max(dist[h] for h in topo.hosts if h != src)
        return [h for h in topo.hosts if h != src and dist[h] == best], best

    def pick_furthest(self, src, rng):
        """Randomly choose one of the furthest hosts (random tie-breaker)."""
        candidates, distance = self.furthest_hosts(src)
        return rng.choice(candidates), candidates, distance

    def diameter_switch_hops(self):
        """Max switch hops between any two hosts (uses host 0 by symmetry)."""
        _, d = self.furthest_hosts(0)
        return d - 1
