"""Dragonfly and Dragonfly+ (paper Table 6 topologies)."""

import contextlib
import io
import random
import unittest

from helpers import failures, routing, run, session, topology
from opp_ping import archinfo, cli

ARCHS = ("dragonfly", "dragonfly-plus")


def groups_of(t):
    return {s: t.coord[s]["group"] for s in t.switches}


def global_links(t):
    grp = groups_of(t)
    return [(a, b) for a, b in t.links
            if not t.is_host(a) and not t.is_host(b) and grp[a] != grp[b]]


class Structure(unittest.TestCase):
    def test_default_sizes(self):
        t = topology.build("dragonfly")
        self.assertEqual((t.num_hosts, t.num_nodes - t.num_hosts, t.params["groups"]), (104, 52, 13))
        t = topology.build("dragonfly-plus")
        self.assertEqual((t.num_hosts, t.num_nodes - t.num_hosts, t.params["groups"]), (72, 72, 9))

    def test_one_global_link_per_group_pair(self):
        for arch in ARCHS:
            for size in (2, 4, 6):
                with self.subTest(arch=arch, size=size):
                    t = topology.generate(arch, size)
                    g = t.params["groups"]
                    grp = groups_of(t)
                    pairs = [frozenset((grp[a], grp[b])) for a, b in global_links(t)]
                    self.assertEqual(len(pairs), g * (g - 1) // 2)
                    self.assertEqual(len(set(pairs)), len(pairs))

    def test_dragonfly_switch_wiring(self):
        t = topology.build("dragonfly")
        a, h, p = t.params["a"], t.params["h"], t.params["p"]
        grp = groups_of(t)
        for s in t.switches:
            hosts = [n for n in t.adj[s] if t.is_host(n)]
            local = [n for n in t.adj[s] if not t.is_host(n) and grp[n] == grp[s]]
            glob = [n for n in t.adj[s] if not t.is_host(n) and grp[n] != grp[s]]
            self.assertEqual((len(hosts), len(local), len(glob)), (p, a - 1, h))

    def test_dragonfly_plus_wiring(self):
        t = topology.build("dragonfly-plus")
        s_, leaves, h, p = (t.params[k] for k in ("s", "leaves", "h", "p"))
        grp = groups_of(t)
        for s in t.switches:
            hosts = [n for n in t.adj[s] if t.is_host(n)]
            local = [n for n in t.adj[s] if not t.is_host(n) and grp[n] == grp[s]]
            glob = [n for n in t.adj[s] if not t.is_host(n) and grp[n] != grp[s]]
            if t.tier[s] == "leaf":
                self.assertEqual((len(hosts), len(local), len(glob)), (p, s_, 0))
                self.assertTrue(all(t.tier[n] == "spine" for n in local))
            else:
                self.assertEqual((len(hosts), len(local), len(glob)), (0, leaves, h))

    def test_paper_presets(self):
        t = topology.build("dragonfly", "paper")
        self.assertEqual((t.num_hosts, t.num_nodes - t.num_hosts, t.params["groups"]),
                         (16512, 2064, 129))
        t = topology.build("dragonfly-plus", "paper")
        self.assertEqual((t.num_hosts, t.num_nodes - t.num_hosts, t.params["groups"]),
                         (67584, 2112, 33))
        with self.assertRaises(ValueError):
            topology.build("ot-purdue", "paper")


class Paths(unittest.TestCase):
    def test_minimal_routing_has_few_paths(self):
        for arch in ARCHS:
            t = topology.build(arch)
            rt = routing.Routing(t)
            far, dist = rt.furthest_hosts(0)
            self.assertEqual(dist - 1, 4)
            counts = [rt.path_count(0, h) for h in far]
            if arch == "dragonfly-plus":
                # minimal routing: leaf -> the one spine owning the global link -> ...
                self.assertEqual(set(counts), {1})
            else:
                # just over half the furthest hosts have a single path; the rest also
                # have equally short routes through an intermediate group (2-7 paths)
                self.assertGreater(counts.count(1), len(counts) / 2.0)
                self.assertLessEqual(max(counts), 7)

    def test_traverse_mentions_global_link(self):
        for arch in ARCHS:
            t = topology.build(arch)
            rt = routing.Routing(t)
            dst = rt.pick_target(0, "furthest", random.Random(1))[0]
            text = rt.traverse_text(0, dst)
            self.assertIn("global link to group", text)
            self.assertTrue(text.startswith("4 switch hops: in group 0:"))
            near = rt.pick_target(0, "closest", random.Random(1))[0]
            self.assertIn("both hosts hang off", rt.traverse_text(0, near))
        t = topology.build("dragonfly")
        rt = routing.Routing(t)
        multi = next(h for h in t.hosts if h and rt.path_count(0, h) > 1)
        self.assertIn("equal-cost paths from group 0", rt.traverse_text(0, multi))

    def test_no_fabric_single_points_of_failure(self):
        for arch in ARCHS:
            t = topology.build(arch)
            rt = routing.Routing(t)
            fabric = [l for l, _ in rt.bridges() if not (t.is_host(l[0]) or t.is_host(l[1]))]
            self.assertEqual(fabric, [])


class Failures(unittest.TestCase):
    def test_default_failure_on_global_link(self):
        for arch in ARCHS:
            t = topology.build(arch)
            rt = routing.Routing(t)
            for seed in range(5):
                rng = random.Random(seed)
                dst = rt.pick_target(0, "furthest", rng)[0]
                f = failures.place("port-down", t, rt, 0, dst, rng)
                self.assertNotEqual(t.coord[f.switch]["group"], t.coord[f.neighbor]["group"])
                self.assertTrue(rt.on_dag(f.switch, 0, dst))

    def test_failures_localized(self):
        for arch in ARCHS:
            for target in ("furthest", "closest"):
                for kind in ("acl-drop", "port-down", "congestion"):
                    for seed in (1, 2, 3):
                        with self.subTest(arch=arch, target=target, kind=kind, seed=seed):
                            _, _, f, res = run(arch, kind, count=1, usm_rate=0.0, seed=seed,
                                               target=target)
                            self.assertEqual(res[0].verdict, "detected", res[0].findings)

    def test_loops(self):
        for arch in ARCHS:
            _, _, f, res = run(arch, "loop", count=12, usm_rate=0.0)
            self.assertEqual(session.tally(res), {"missed": 12})
            _, _, f, res = run(arch, "loop", count=12, usm_rate=1.0)
            hits = [r for r in res if r.verdict == "detected"]
            self.assertTrue(hits)
            self.assertTrue(all(r.misconfigured_switch == f.switch for r in hits))


class Info(unittest.TestCase):
    def test_op_cache_points_to_solver(self):
        for arch in ARCHS:
            entries, conc, text = archinfo.op_cache(arch, 4)
            self.assertIsNone(entries)
            self.assertIn("solver/unit-merge", text)
            self.assertIn("4096", text)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli.main(["info", "--arch", arch])
            out = buf.getvalue()
            self.assertIn("reproduce_six_tor_scope.sh %s" % arch, out)
            self.assertIn("--size paper", out)


if __name__ == "__main__":
    unittest.main()
