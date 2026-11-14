import unittest

from helpers import ARCHES, baseline_mod as baseline, engine, setup


class Baselines(unittest.TestCase):
    def test_healthy_pings(self):
        for arch in ARCHES:
            topo, rt, rng, src, dst, fail = setup(arch)
            rep = baseline.run(topo, rt, fail, rng, src, dst, 16)
            self.assertTrue(all(s.delivered and not s.reply_lost for s in rep.pings))
            base = 2 * rt.hops(src, dst) * (engine.LINK_DELAY_NS + engine.TX_NS)
            self.assertTrue(all(s.rtt_ns == base for s in rep.pings))
            self.assertIsNone(rep.trace)
            self.assertEqual(rep.rpingmesh + rep.rdprobe, [])

    def test_one_ping_covers_one_path(self):
        topo, rt, rng, src, dst, fail = setup("fat-tree", size=8)
        rep = baseline.run(topo, rt, fail, rng, src, dst, 1)
        self.assertEqual(rep.paths_seen, 1)
        self.assertEqual(rep.paths_total, 16)

    def test_coupon_collector(self):
        self.assertAlmostEqual(baseline.coupon_collector(1), 1.0)
        self.assertAlmostEqual(baseline.coupon_collector(2), 3.0)
        self.assertGreater(baseline.coupon_collector(1024), 7000)

    def test_mirror_baseline_localizes_with_enough_pings(self):
        for kind in ("acl-drop", "congestion", "port-down"):
            with self.subTest(kind=kind):
                topo, rt, rng, src, dst, fail = setup("fat-tree", kind=kind)
                rep = baseline.run(topo, rt, fail, rng, src, dst, 256)
                self.assertIn(fail.link, [link for _, link in rep.rdprobe_links])
                self.assertIsNotNone(rep.trace)
                self.assertTrue(rep.rpingmesh)

    def test_traceroute_shows_path_ambiguity(self):
        topo, rt, rng, src, dst, fail = setup("fat-tree", size=8, kind="acl-drop")
        rep = baseline.run(topo, rt, fail, rng, src, dst, 64)
        responders_ttl2 = {s.responder for s in rep.trace[1][1] if s.rtt_ns is not None}
        self.assertGreater(len(responders_ttl2), 1)     # one TTL, many candidate switches

    def test_loop_seen_as_unexpected_ttl(self):
        topo, rt, rng, src, dst, fail = setup("leaf-spine", kind="loop", loop_prob=1.0)
        rep = baseline.run(topo, rt, fail, rng, src, dst, 256)
        self.assertIn(("misroute", (fail.switch, fail.wrong_port)), rep.rdprobe_links)


if __name__ == "__main__":
    unittest.main()
