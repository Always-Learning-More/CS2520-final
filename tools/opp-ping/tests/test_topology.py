import os
import tempfile
import unittest

from helpers import topology


class TopologySizes(unittest.TestCase):
    def check(self, topo, hosts, switches, links):
        self.assertEqual(topo.num_hosts, hosts)
        self.assertEqual(topo.num_nodes - topo.num_hosts, switches)
        self.assertEqual(len(topo.links), links)

    def test_fat_tree(self):
        self.check(topology.fat_tree(4), 16, 20, 48)
        t = topology.fat_tree(8)
        self.check(t, 128, 80, 384)
        self.assertEqual(t.tier_counts(), {"host": 128, "tor": 32, "agg": 32, "core": 16})

    def test_leaf_spine(self):
        t = topology.leaf_spine(4)
        self.check(t, 8, 6, 16)
        self.assertEqual(t.tier_counts(), {"host": 8, "leaf": 4, "spine": 2})

    def test_tori(self):
        self.check(topology.torus_2d(4), 16, 16, 48)
        self.check(topology.torus_3d(3), 27, 27, 108)

    def test_every_switch_port_count(self):
        t = topology.fat_tree(8)
        for s in t.switches:
            self.assertEqual(len(t.adj[s]), 8)          # radix k everywhere
        t = topology.torus_3d(4)
        for s in t.switches:
            self.assertEqual(len(t.adj[s]), 7)          # 6 torus links + 1 host

    def test_invalid_sizes(self):
        for fn, bad in ((topology.fat_tree, 3), (topology.leaf_spine, 5),
                        (topology.torus_2d, 2), (topology.torus_3d, 1)):
            with self.assertRaises(ValueError):
                fn(bad)


class Addressing(unittest.TestCase):
    def test_simulator_ip_scheme(self):
        self.assertEqual(topology.node_id_to_ip(0), "11.0.0.1")
        self.assertEqual(topology.node_id_to_ip(255), "11.0.255.1")
        self.assertEqual(topology.node_id_to_ip(256), "11.1.0.1")
        self.assertEqual(topology.node_id_to_ip(65535), "11.255.255.1")
        for n in (0, 1, 255, 256, 1000, 65535):
            self.assertEqual(topology.ip_to_node_id(topology.node_id_to_ip(n)), n)

    def test_bad_ips(self):
        for bad in ("10.0.0.1", "11.0.0.2", "11.0.300.1", "hello", "11.0.0"):
            with self.assertRaises(ValueError):
                topology.ip_to_node_id(bad)

    def test_lookup(self):
        t = topology.fat_tree(4)
        self.assertEqual(t.lookup("11.0.5.1"), 5)
        self.assertEqual(t.lookup("host-5"), 5)
        self.assertEqual(t.lookup("5"), 5)
        agg = t.lookup("agg-p1-0")
        self.assertEqual(t.tier[agg], "agg")
        self.assertEqual(t.lookup(t.ip(agg)), agg)
        with self.assertRaises(ValueError):
            t.lookup("11.0.200.1")
        with self.assertRaises(ValueError):
            t.lookup("nope-3")


class RepoParity(unittest.TestCase):
    """Generated topologies match the ns-3 topology files link for link."""

    def test_matches_repo_files(self):
        root = topology.repo_topology_dir()
        if root is None:
            self.skipTest("repo topology files not found")
        for (arch, size), rel in sorted(topology.REPO_TOPOLOGY_FILES.items()):
            path = root / rel
            if not path.exists():
                continue
            with self.subTest(arch=arch, size=size):
                num_nodes, switches, links = topology.read_topology_file(path)
                t = topology.build(arch, size)
                self.assertEqual(num_nodes, t.num_nodes)
                self.assertEqual(switches, list(t.switches))
                self.assertEqual(links, t.links)        # same order -> same port numbers

    def test_file_round_trip(self):
        t = topology.torus_2d(5)
        fd, path = tempfile.mkstemp(suffix=".txt")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(t.to_file_text())
            n, sw, links = topology.read_topology_file(path)
        finally:
            os.unlink(path)
        self.assertEqual((n, sw, links), (t.num_nodes, list(t.switches), t.links))


if __name__ == "__main__":
    unittest.main()
