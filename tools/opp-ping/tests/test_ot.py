"""OT (Purdue-model) plant network: no redundancy, a fixed 1 -> 2 -> 4 -> chains
pattern (chains of 12: 52 hosts, 51 switches), probed from 11.0.11.1 (dev-1a-8,
low in cell 1a) to its closest and furthest devices, with failures placed on
different nodes than the Clos tests."""

import contextlib
import io
import random
import unittest

from helpers import baseline_mod as baseline, engine, failures, routing, run, session, topology
from opp_ping import cli

SRC_IP = "11.0.11.1"                     # dev-1a-8
SRC = 11
TARGETS = ("closest", "furthest")
CLOSEST = ["11.0.10.1", "11.0.12.1"]     # dev-1a-7, dev-1a-9
FURTHEST = ["11.0.39.1", "11.0.51.1"]    # dev-2a-12, dev-2b-12


def ot(n=12):
    t = topology.generate("ot-purdue", n)
    return t, routing.Routing(t)


def path_links(topo, rt, src, dst):
    """Every (switch, egress port) on the single src -> dst path."""
    return [(s, rt.ecmp_ports(s, dst)[0]) for s in rt.dag_switches(src, dst)]


class Shape(unittest.TestCase):
    def test_sizes(self):
        for n, hosts, switches in ((1, 8, 7), (3, 16, 15), (12, 52, 51), (20, 84, 83)):
            t, _ = ot(n)
            self.assertEqual((t.num_hosts, t.num_nodes - t.num_hosts, len(t.links)),
                             (hosts, switches, hosts + switches - 1))

    def test_default_meets_the_floor(self):
        t = topology.build("ot-purdue")
        self.assertEqual((t.num_hosts, t.num_nodes - t.num_hosts), (52, 51))
        with self.assertRaises(ValueError):
            topology.build("ot-purdue", 11)              # 48 hosts, 47 switches

    def test_pattern_1_2_4_chains(self):
        t, rt = ot()
        self.assertEqual(t.tier_counts(), {"host": 52, "core": 1, "area": 2, "cell": 48})
        core = t.lookup("core")
        areas = [nb for nb in t.adj[core] if not t.is_host(nb)]
        self.assertEqual(sorted(t.name[a] for a in areas), ["area-1", "area-2"])
        for a in areas:
            cells = [t.name[nb] for nb in t.adj[a] if t.tier[nb] == "cell"]
            self.assertEqual(len(cells), 2)
            self.assertTrue(all(c.endswith("-s1") for c in cells))
        for cell in ("1a", "1b", "2a", "2b"):
            for i in range(1, 13):
                sw = t.lookup("%s-s%d" % (cell, i))
                hosts = [nb for nb in t.adj[sw] if t.is_host(nb)]
                self.assertEqual([t.name[h] for h in hosts], ["dev-%s-%d" % (cell, i)])
                chain = [nb for nb in t.adj[sw] if t.tier[nb] == "cell"]
                self.assertEqual(len(chain), 1 if i == 12 else 2 if i > 1 else 1)

    def test_every_switch_has_one_uplink(self):
        t, rt = ot()
        core = t.lookup("core")
        for s in t.switches:
            if s == core:
                continue
            up = [nb for nb in t.adj[s] if not t.is_host(nb) and rt.hops(nb, core) < rt.hops(s, core)]
            self.assertEqual(len(up), 1, t.name[s])

    def test_no_redundancy_anywhere(self):
        t, rt = ot()
        self.assertEqual(len(rt.bridges()), len(t.links))          # it is a tree
        for a in range(0, t.num_hosts, 3):
            for b in t.hosts:
                if a < b:
                    self.assertEqual(rt.path_count(a, b), 1)

    def test_addressing(self):
        t, _ = ot()
        expect = {"historian": "11.0.0.1", "eng-ws": "11.0.1.1", "hmi-1": "11.0.2.1",
                  "hmi-2": "11.0.3.1", "dev-1a-1": "11.0.4.1", "dev-1a-7": "11.0.10.1",
                  "dev-1a-8": "11.0.11.1", "dev-1a-9": "11.0.12.1", "dev-1a-12": "11.0.15.1",
                  "dev-1b-1": "11.0.16.1", "dev-2a-1": "11.0.28.1", "dev-2a-12": "11.0.39.1",
                  "dev-2b-12": "11.0.51.1"}
        for name, ip in expect.items():
            self.assertEqual(t.ip(t.lookup(name)), ip)
        self.assertEqual(t.name[t.attached_switch(t.lookup(SRC_IP))], "1a-s8")

    def test_diameter(self):
        self.assertEqual(ot()[1].diameter_switch_hops(), 27)      # chain end to chain end


class Targets(unittest.TestCase):
    def setUp(self):
        self.t, self.rt = ot()
        self.src = self.t.lookup(SRC_IP)

    def ips(self, hosts):
        return sorted(self.t.ip(h) for h in hosts)

    def test_closest_and_furthest_sets(self):
        near, d = self.rt.closest_hosts(self.src)
        self.assertEqual(self.ips(near), CLOSEST)
        self.assertEqual(d - 1, 2)
        far, d = self.rt.furthest_hosts(self.src)
        self.assertEqual(self.ips(far), FURTHEST)
        self.assertEqual(d - 1, 23)

    def test_random_tie_break_reaches_both(self):
        for target in TARGETS:
            picks = {self.rt.pick_target(self.src, target, random.Random(s))[0] for s in range(30)}
            self.assertEqual(len(picks), 2, target)

    def test_route_and_traverse_text(self):
        far = self.t.lookup("11.0.39.1")
        chain_up = " -> ".join("1a-s%d" % i for i in range(8, 0, -1))
        chain_down = " -> ".join("2a-s%d" % i for i in range(1, 13))
        self.assertEqual(self.rt.route_text(self.src, far),
                         "11.0.11.1 dev-1a-8 -> %s -> area-1 -> core -> area-2 -> %s "
                         "-> dev-2a-12 11.0.39.1" % (chain_up, chain_down))
        self.assertEqual(self.rt.traverse_text(self.src, far),
                         "23 switch hops: up the cell-1a chain 1a-s8 .. 1a-s1 (8 switches) "
                         "-> area-1 -> core; down area-2 -> the cell-2a chain 2a-s1 .. 2a-s12 "
                         "(12 switches)")
        self.assertEqual(self.rt.traverse_text(self.src, self.t.lookup("11.0.10.1")),
                         "2 switch hops: up the cell-1a chain: 1a-s8 -> 1a-s7")
        self.assertEqual(self.rt.traverse_text(self.src, self.t.lookup("11.0.12.1")),
                         "2 switch hops: down the cell-1a chain: 1a-s8 -> 1a-s9")

    def test_default_failure_is_on_a_different_node(self):
        src_switch = self.t.attached_switch(self.src)
        for seed in range(6):
            rng = random.Random(seed)
            far = self.rt.pick_target(self.src, "furthest", rng)[0]
            f = failures.place("port-down", self.t, self.rt, self.src, far, rng)
            self.assertEqual(self.t.name[f.switch], "area-2")      # the target's own area
            self.assertNotEqual(f.switch, src_switch)
            self.assertEqual(self.t.tier[f.neighbor], "cell")      # downlink into the target cell
            self.assertEqual(self.t.coord[f.neighbor]["cell"], self.t.coord[far]["cell"])
            near = self.rt.pick_target(self.src, "closest", rng)[0]
            f = failures.place("port-down", self.t, self.rt, self.src, near, rng)
            self.assertEqual(f.switch, src_switch)                 # the only chain link
            self.assertEqual(f.neighbor, self.t.attached_switch(near))


class FailuresOnEveryLink(unittest.TestCase):
    def test_every_link_every_failure_one_probe(self):
        t, rt = ot()
        src = t.lookup(SRC_IP)
        for target in TARGETS:
            for dst in getattr(rt, "%s_hosts" % target)(src)[0]:
                for sw, port in path_links(t, rt, src, dst):
                    for kind in ("acl-drop", "port-down", "congestion"):
                        for usm in (True, False):
                            with self.subTest(target=target, dst=t.ip(dst), link=t.link_name(sw, port),
                                              kind=kind, usm=usm):
                                rng = random.Random(1)
                                f = failures.place(kind, t, rt, src, dst, rng,
                                                   fail_at="%s:%d" % (t.name[sw], port))
                                r = session.Session(t, rt, f, rng, src, dst).run(1, 1.0 if usm else 0.0)[0]
                                self.assertEqual(r.verdict, "detected", r.findings)

    def test_link_flap(self):
        for target in TARGETS:
            with self.subTest(target=target):
                _, _, f, res = run("ot-purdue", "link-flap", count=40, usm_rate=0.1, seed=6,
                                   target=target, src=SRC)
                tally = session.tally(res)
                self.assertGreater(tally.get("detected", 0), 0)
                self.assertEqual(tally.get("wrong", 0) + tally.get("partial", 0), 0)

    def test_loop_needs_usm(self):
        for target in TARGETS:
            with self.subTest(target=target):
                _, _, f, res = run("ot-purdue", "loop", count=15, usm_rate=0.0, target=target, src=SRC)
                self.assertEqual(session.tally(res), {"missed": 15})
                _, _, f, res = run("ot-purdue", "loop", count=15, usm_rate=1.0, target=target, src=SRC)
                hits = [r for r in res if r.verdict == "detected"]
                self.assertTrue(hits)
                self.assertTrue(all(r.misconfigured_switch == f.switch for r in hits))
                self.assertEqual(session.tally(res).get("wrong", 0), 0)

    def test_healthy_single_path_behaviour(self):
        for target in TARGETS:
            for usm in (1.0, 0.0):
                topo, rt, f, res = run("ot-purdue", count=1, usm_rate=usm, target=target, src=SRC)
                r = res[0]
                switches = rt.dag_switches(r.src, r.dst)
                self.assertEqual(r.path_count, 1)
                self.assertEqual(r.recirculations, 0)               # nothing to duplicate
                self.assertEqual(sum(r.switch_probe_rx.values()), len(switches))
                self.assertEqual(sum(r.switch_ack_rx.values()), len(switches))
                self.assertEqual(r.rtt_ns, 2 * rt.hops(r.src, r.dst) *
                                 (engine.LINK_DELAY_NS + engine.TX_NS))


class BlastRadius(unittest.TestCase):
    def test_cut_off_hosts(self):
        t, rt = ot()
        src = t.lookup(SRC_IP)

        def cut(a, b):
            return sorted(t.name[h] for h in rt.cut_off_hosts(src, t.lookup(a), t.lookup(b)))

        self.assertEqual(len(cut("core", "area-2")), 25)          # hmi-2 + 24 devices
        self.assertEqual(len(cut("core", "area-1")), 27)          # everything outside area 1
        self.assertEqual(cut("area-2", "2a-s1"), sorted("dev-2a-%d" % i for i in range(1, 13)))
        self.assertEqual(len(cut("2a-s1", "2a-s2")), 11)
        self.assertEqual(cut("1a-s8", "1a-s9"), sorted("dev-1a-%d" % i for i in range(9, 13)))
        self.assertEqual(len(cut("1a-s8", "1a-s7")), 52 - 1 - 4)  # everything above the source

    def test_worst_outage(self):
        t, rt = ot()
        worst = max(rt.bridges(), key=lambda b: b[1])
        self.assertEqual(worst[1], 25)
        self.assertIn("core", {t.name[x] for x in worst[0]})


def setup_ot(target, kind="none", seed=1):
    t, rt = ot()
    rng = random.Random(seed)
    dst = rt.pick_target(SRC, target, rng)[0]
    return t, rt, rng, SRC, dst, failures.place(kind, t, rt, SRC, dst, rng)


class Baselines(unittest.TestCase):
    def test_no_path_ambiguity_without_redundancy(self):
        t, rt, rng, src, dst, f = setup_ot("furthest", "acl-drop")
        rep = baseline.run(t, rt, f, rng, src, dst, 16)
        self.assertEqual((rep.paths_seen, rep.paths_total), (0, 1))  # all lost, 1 path exists
        for ttl, samples in rep.trace:
            responders = {s.responder for s in samples if s.rtt_ns is not None}
            self.assertLessEqual(len(responders), 1, ttl)       # one switch per TTL
        self.assertIn(f.link, [link for _, link in rep.rdprobe_links])
        self.assertEqual(rep.rpingmesh_links[0], ("loss", f.neighbor))

    def test_one_ping_covers_the_only_path(self):
        t, rt, rng, src, dst, f = setup_ot("closest")
        rep = baseline.run(t, rt, f, rng, src, dst, 1)
        self.assertEqual((rep.paths_seen, rep.paths_total), (1, 1))


def call(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(list(argv))
    return buf.getvalue()


class Cli(unittest.TestCase):
    def test_ping_closest_output(self):
        out = call("ping", SRC_IP, "--arch", "ot-purdue", "--target", "closest",
                   "--failure", "port-down", "-c", "1", "--seed", "2")
        self.assertIn("[closest device]", out)
        self.assertIn("is the closest device: 2 switch hops away", out)
        self.assertIn(", ".join(CLOSEST), out)
        self.assertIn("route            : 11.0.11.1 dev-1a-8 -> 1a-s8 ->", out)
        self.assertIn("traverse         : 2 switch hops:", out)
        self.assertIn("blast radius", out)
        self.assertIn("[detected]", out)

    def test_ping_furthest_output(self):
        out = call("ping", SRC_IP, "--arch", "ot-purdue", "--failure", "congestion",
                   "-c", "1", "--seed", "2")
        self.assertIn("[furthest device]", out)
        self.assertIn("23 switch hops away", out)
        self.assertIn(", ".join(FURTHEST), out)
        self.assertIn("up the cell-1a chain 1a-s8 .. 1a-s1 (8 switches) -> area-1 -> core; down area-2", out)
        self.assertIn("CONGESTION area-2 -> 2", out)

    def test_info_and_demo(self):
        out = call("info", "--arch", "ot-purdue")
        self.assertIn("11.0.11.1 dev-1a-8", out)
        self.assertIn("50 of 50 fabric links", out)
        self.assertIn("cuts off 25 hosts", out)
        out = call("demo", "--arch", "ot-purdue", "--runs", "1", "--max-exp", "1",
                   "--seed", "1", "--no-csv")
        self.assertIn("FURTHEST device from 11.0.11.1", out)
        self.assertIn("CLOSEST device from 11.0.11.1", out)
        self.assertEqual(out.count("    route            : 11.0.11.1"), 2)


if __name__ == "__main__":
    unittest.main()
