"""Behaviour of the OPP data-plane model on every architecture."""

import random
import unittest

from helpers import ARCHES, engine, failures, routing, run, session, setup, topology

SEEDS = (1, 2, 3)


def dag_edges(topo, rt, src, dst):
    """Equal-cost edges (u -> v) from src to dst, including the host links."""
    edges = [(src, topo.attached_switch(src))]
    for s in rt.dag_switches(src, dst):
        for p in rt.ecmp_ports(s, dst):
            edges.append((s, topo.neighbor(s, p)))
    return edges


class Healthy(unittest.TestCase):
    def test_one_probe_covers_every_path(self):
        for arch in ARCHES:
            for usm in (1.0, 0.0):
                with self.subTest(arch=arch, usm=usm):
                    topo, rt, fail, res = run(arch, "none", count=1, usm_rate=usm)
                    r = res[0]
                    self.assertIsNotNone(r.ack)
                    self.assertEqual(r.path_count, r.expected_paths)
                    self.assertEqual(r.findings, [])
                    self.assertEqual(r.drops, [])
                    self.assertEqual(r.verdict, "ok")

    def test_each_link_carries_one_probe_and_one_ack(self):
        """Technique 2: duplication + deduplication -> every equal-cost link
        is traversed by exactly one probe and one ACK."""
        for arch in ARCHES:
            with self.subTest(arch=arch):
                topo, rt, fail, res = run(arch, "none", count=1, usm_rate=0.0)
                r = res[0]
                src, dst = r.src, r.dst
                edges = dag_edges(topo, rt, src, dst)
                probe_rx = sum(1 for u, v in edges if not topo.is_host(v))
                ack_rx = sum(1 for u, v in edges if not topo.is_host(u))
                self.assertEqual(sum(r.switch_probe_rx.values()), probe_rx)
                self.assertEqual(sum(r.switch_ack_rx.values()), ack_rx)

    def test_native_rtt_is_base_rtt(self):
        for arch in ARCHES:
            topo, rt, fail, res = run(arch, "none", count=1, usm_rate=0.0)
            r = res[0]
            hops = rt.hops(r.src, r.dst)
            self.assertEqual(r.rtt_ns, 2 * hops * (engine.LINK_DELAY_NS + engine.TX_NS))
            self.assertEqual(r.recirculations, 0)

    def test_usm_recirculates_only_where_it_duplicates(self):
        topo, rt, fail, res = run("fat-tree", "none", count=1, usm_rate=1.0, size=8)
        self.assertGreater(res[0].recirculations, 0)
        topo, rt, fail, res = run("fat-tree", "none", count=1, usm_rate=1.0, size=8)
        self.assertGreaterEqual(res[0].rtt_ns, 2 * 6 * engine.LINK_DELAY_NS)

    def test_usm_recirculations_at_paper_scale(self):
        """Paper (App. E.2): covering 32 uplinks needs ~130 recirculations."""
        topo = topology.fat_tree(64)
        rt = routing.Routing(topo)
        rng = random.Random(5)
        dst = rt.pick_furthest(0, rng)[0]
        net = engine.OppNetwork(topo, rt, failures.Failure("none", topo), rng)
        r = net.probe(0, dst, 1, usm=True)
        self.assertEqual(r.path_count, 1024)
        dup_switches = [s for s in rt.dag_switches(0, dst) if len(rt.ecmp_ports(s, dst)) == 32]
        per_switch = r.recirculations / float(len(dup_switches))
        self.assertTrue(80 < per_switch < 200, per_switch)


class Failures(unittest.TestCase):
    def test_deterministic_failures_localized_by_one_probe(self):
        for arch in ARCHES:
            for kind in ("acl-drop", "port-down", "congestion"):
                for seed in SEEDS:
                    for usm in (1.0, 0.0):
                        with self.subTest(arch=arch, kind=kind, seed=seed, usm=usm):
                            topo, rt, fail, res = run(arch, kind, count=1, usm_rate=usm, seed=seed)
                            r = res[0]
                            self.assertEqual(r.verdict, "detected", r.findings)
                            want = session.EXPECTED_KIND[kind]
                            self.assertEqual([(k, l) for k, l, _ in r.findings if k == want],
                                             [(want, fail.link)])

    def test_loss_reduces_reported_path_count(self):
        topo, rt, fail, res = run("fat-tree", "port-down", count=1, size=8)
        r = res[0]
        self.assertLess(r.path_count, r.expected_paths)
        self.assertIsNotNone(r.ack.faulty_link)

    def test_congestion_value(self):
        topo, rt, fail, res = run("leaf-spine", "congestion", count=1, congestion_us=300.0)
        self.assertEqual(res[0].ack.max_qdelay, 300 * 1000)
        topo, rt, fail, res = run("leaf-spine", "congestion", count=1, congestion_us=50.0)
        self.assertEqual(res[0].findings, [])       # below T_qdelay = 100 us

    def test_link_flap_detected_and_never_mislocalized(self):
        for arch in ARCHES:
            with self.subTest(arch=arch):
                topo, rt, fail, res = run(arch, "link-flap", count=40, usm_rate=0.1, seed=4)
                verdicts = session.tally(res)
                self.assertGreater(verdicts.get("detected", 0), 0)
                self.assertEqual(verdicts.get("wrong", 0), 0)
                self.assertEqual(verdicts.get("detected", 0) + verdicts.get("missed", 0), 40)

    def test_loop_hidden_from_native_multicast(self):
        """Section 4.3: native multicast never consults the broken unicast table."""
        for arch in ARCHES:
            with self.subTest(arch=arch):
                topo, rt, fail, res = run(arch, "loop", count=15, usm_rate=0.0)
                self.assertEqual(session.tally(res), {"missed": 15})

    def test_loop_found_and_pinned_by_usm(self):
        for arch in ARCHES:
            for seed in SEEDS:
                with self.subTest(arch=arch, seed=seed):
                    topo, rt, fail, res = run(arch, "loop", count=15, usm_rate=1.0, seed=seed)
                    t = session.tally(res)
                    self.assertGreater(t.get("detected", 0), 0, t)
                    self.assertEqual(t.get("wrong", 0), 0, t)
                    for r in res:
                        if r.verdict == "detected":
                            self.assertEqual(r.misconfigured_switch, fail.switch)

    def test_loop_always_exposed_when_misrouting_is_certain_after_retry(self):
        topo, rt, fail, res = run("fat-tree", "loop", count=10, usm_rate=1.0, loop_prob=0.9)
        self.assertGreaterEqual(session.tally(res).get("detected", 0), 7)

    def test_usm_sampling_bounds_loop_detection_latency(self):
        """With sampling p, a loop is exposed within ceil(1/p) probes."""
        topo, rt, fail, res = run("leaf-spine", "loop", count=40, usm_rate=0.25,
                                  seed=2, loop_prob=1.0)
        usm_probes = [r for r in res if r.usm]
        self.assertEqual(len(usm_probes), 10)
        self.assertTrue(all(not r.findings for r in res if not r.usm))
        self.assertTrue(any(r.findings for r in usm_probes))
        first = min(r.index for r in res if r.findings)
        self.assertLessEqual(first, 4 + 4)          # within two USM rounds of ceil(1/p)=4

    def test_host_link_loss_reported_by_local_timer(self):
        topo, rt, rng, src, dst, _ = setup("fat-tree")
        tor = topo.attached_switch(src)
        fail = failures.place("port-down", topo, rt, src, dst, rng,
                              fail_link="%s,%s" % (topo.name[tor], topo.name[src]))
        r = session.Session(topo, rt, fail, rng, src, dst).run(1)[0]
        self.assertIsNone(r.ack)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.findings[0][:2], ("loss", (src, 0)))

    def test_failure_off_the_path_is_invisible(self):
        topo, rt, rng, src, dst, _ = setup("fat-tree", size=8)
        other_pod_agg = next(s for s in topo.switches
                             if topo.tier[s] == "agg" and not rt.on_dag(s, src, dst))
        fail = failures.place("port-down", topo, rt, src, dst, rng, fail_at=topo.name[other_pod_agg])
        r = session.Session(topo, rt, fail, rng, src, dst).run(1)[0]
        self.assertEqual(r.findings, [])
        self.assertEqual(r.verdict, "missed")

    def test_explicit_placement(self):
        topo, rt, rng, src, dst, _ = setup("2d-torus")
        first = topo.attached_switch(src)
        port = rt.ecmp_ports(first, dst)[0]
        fail = failures.place("acl-drop", topo, rt, src, dst, rng,
                              fail_at="%s:%d" % (topo.name[first], port))
        self.assertEqual(fail.link, (first, port))
        r = session.Session(topo, rt, fail, rng, src, dst).run(1)[0]
        self.assertEqual(r.verdict, "detected")

    def test_placement_errors(self):
        topo, rt, rng, src, dst, _ = setup("fat-tree")
        with self.assertRaises(ValueError):
            failures.place("acl-drop", topo, rt, src, dst, rng, fail_link="core-0,core-1")
        with self.assertRaises(ValueError):
            failures.place("acl-drop", topo, rt, src, dst, rng, fail_at="host-3")
        with self.assertRaises(ValueError):
            failures.place("bogus", topo, rt, src, dst, rng)


class Schedule(unittest.TestCase):
    def test_round_robin_usm(self):
        rng = random.Random(0)
        s = session.usm_schedule(30, 0.1, rng)
        self.assertEqual(sum(s), 3)
        gaps = [i for i, v in enumerate(s) if v]
        self.assertEqual([b - a for a, b in zip(gaps, gaps[1:])], [10, 10])
        self.assertEqual(session.usm_schedule(5, 1.0, rng), [True] * 5)
        self.assertEqual(session.usm_schedule(5, 0.0, rng), [False] * 5)


if __name__ == "__main__":
    unittest.main()
