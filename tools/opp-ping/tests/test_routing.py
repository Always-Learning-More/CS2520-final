import random
import unittest

from helpers import ARCHES, routing, topology


class Furthest(unittest.TestCase):
    def test_furthest_is_maximal(self):
        for arch in ARCHES:
            t = topology.build(arch)
            rt = routing.Routing(t)
            for src in (0, t.num_hosts // 2, t.num_hosts - 1):
                cands, dist = rt.furthest_hosts(src)
                self.assertTrue(cands)
                for h in t.hosts:
                    if h != src:
                        self.assertLessEqual(rt.hops(src, h), dist)
                for c in cands:
                    self.assertEqual(rt.hops(src, c), dist)

    def test_random_tie_break(self):
        t = topology.fat_tree(4)
        rt = routing.Routing(t)
        cands, _ = rt.furthest_hosts(0)
        self.assertEqual(len(cands), 12)            # every host outside pod 0
        picks = {rt.pick_furthest(0, random.Random(s))[0] for s in range(60)}
        self.assertGreater(len(picks), 5)
        self.assertTrue(picks <= set(cands))

    def test_seed_is_reproducible(self):
        t = topology.leaf_spine(8)
        rt = routing.Routing(t)
        a = rt.pick_furthest(3, random.Random(42))[0]
        b = rt.pick_furthest(3, random.Random(42))[0]
        self.assertEqual(a, b)

    def test_unique_furthest_in_even_2d_torus(self):
        t = topology.torus_2d(4)
        rt = routing.Routing(t)
        cands, _ = rt.furthest_hosts(0)
        self.assertEqual([t.coord[c] for c in cands], [{"x": 2, "y": 2}])


class Paths(unittest.TestCase):
    def furthest_paths(self, t):
        rt = routing.Routing(t)
        dst = rt.furthest_hosts(0)[0][0]
        return rt, dst, rt.path_count(0, dst)

    def test_path_counts(self):
        self.assertEqual(self.furthest_paths(topology.fat_tree(4))[2], 4)       # (k/2)^2
        self.assertEqual(self.furthest_paths(topology.fat_tree(8))[2], 16)
        self.assertEqual(self.furthest_paths(topology.leaf_spine(8))[2], 4)     # k/2
        self.assertEqual(self.furthest_paths(topology.torus_2d(4))[2], 24)      # 2*2*C(4,2)
        self.assertEqual(self.furthest_paths(topology.torus_3d(3))[2], 6)       # 3!

    def test_paper_scale_fat_tree(self):
        self.assertEqual(self.furthest_paths(topology.fat_tree(64))[2], 1024)

    def test_diameters(self):
        expect = {"fat-tree": 5, "leaf-spine": 3, "2d-torus": 5, "3d-torus": 4}
        for arch, d in expect.items():
            self.assertEqual(routing.Routing(topology.build(arch)).diameter_switch_hops(), d)

    def test_ecmp_ports_move_closer(self):
        for arch in ARCHES:
            t = topology.build(arch)
            rt, dst, _ = self.furthest_paths(t)
            dist = rt.dist_to(dst)
            for s in rt.dag_switches(0, dst):
                ports = rt.ecmp_ports(s, dst)
                self.assertTrue(ports)
                for p in ports:
                    self.assertEqual(dist[t.neighbor(s, p)], dist[s] - 1)

    def test_fat_tree_fanout(self):
        t = topology.fat_tree(8)
        rt, dst, _ = self.furthest_paths(t)
        self.assertEqual(len(rt.ecmp_ports(t.attached_switch(0), dst)), 4)


if __name__ == "__main__":
    unittest.main()
