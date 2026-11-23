"""The probe-budget sweep behind the demo (paper Figures 8 and 9)."""

import unittest

from helpers import topology  # noqa: F401  (sets sys.path)
from opp_ping import report, sweep

PARAMS = {"flap_prob": 0.2, "congestion_us": 500.0, "loop_prob": 0.5}


def task(arch, seed, max_exp=4, targets=("furthest", "closest"), src=None, size=None):
    if src is None:
        src = 11 if arch == "ot-purdue" else 0
    return (arch, size, src, targets, seed, max_exp, PARAMS)


def by_key(rows):
    out = {}
    for r in rows:
        out[(r["target"], r["failure"], r["method"], r["probes_per_task"])] = r
    return out


class Budgets(unittest.TestCase):
    def test_powers_of_two(self):
        self.assertEqual(sweep.budgets(8), [1, 2, 4, 8, 16, 32, 64, 128, 256])
        self.assertEqual(sweep.budgets(0), [1])

    def test_rows_per_task(self):
        rows, cases = sweep.run_task(task("fat-tree", 1, max_exp=3))
        self.assertEqual(len(rows), 2 * 6 * 6 * 4)      # targets x failures x methods x budgets
        self.assertEqual(len(cases), 2 * 6)

    def test_outcomes_never_get_worse_with_more_probes(self):
        """Budget n is the first n probes of one run, so detection and
        localization are monotone in n (like the paper's curves)."""
        for arch in ("fat-tree", "2d-torus", "ot-purdue", "dragonfly"):
            rows, _ = sweep.run_task(task(arch, 3))
            k = by_key(rows)
            for (target, failure, method, n), r in k.items():
                if failure == "none" or n == 1:
                    continue
                prev = k[(target, failure, method, n // 2)]
                self.assertGreaterEqual(r["detected"], prev["detected"])
                self.assertGreaterEqual(r["localized"], prev["localized"])

    def test_probes_sent(self):
        rows, _ = sweep.run_task(task("fat-tree", 2, max_exp=3, targets=("furthest",)))
        hops = 5
        for r in rows:
            n = r["probes_per_task"]
            if r["method"] in ("Ping", "RD-Probe"):
                self.assertEqual(r["probes_sent"], n)
            elif r["method"] == "R-Pingmesh":
                # paper: as many Traceroute probes per TTL as Ping probes
                self.assertIn(r["probes_sent"], (n, n * (1 + hops)))
            elif r["failure"] != "loop":
                self.assertEqual(r["probes_sent"], n)
            else:
                self.assertGreaterEqual(r["probes_sent"], n)   # + loop re-injections

    def test_paper_patterns(self):
        rows = []
        for seed in range(1, 9):
            rows += sweep.run_task(task("fat-tree", seed, max_exp=5, targets=("furthest",)))[0]
        agg = {(r["failure"], r["method"], r["probes_per_task"]): r for r in report.aggregate(rows)}
        # one OPP probe localizes deterministic failures exactly
        for failure in ("acl-drop", "port-down", "congestion"):
            for m in ("OPP_Sample_0", "OPP_Sample_10", "OPP_Sample_100"):
                self.assertEqual(agg[(failure, m, 1)]["localization_f1"], 1.0)
            # host-generated probes need many more on 16 equal-cost paths
            self.assertLess(agg[(failure, "RD-Probe", 1)]["detection_recall"], 0.5)
        # Figure 8b: native multicast never sees a loop; 100% USM sees it soonest
        for n in (1, 2, 4, 8, 16, 32):
            self.assertEqual(agg[("loop", "OPP_Sample_0", n)]["detection_recall"], 0.0)
        self.assertGreater(agg[("loop", "OPP_Sample_100", 1)]["detection_recall"],
                           agg[("loop", "OPP_Sample_10", 1)]["detection_recall"])
        self.assertEqual(agg[("loop", "OPP_Sample_10", 32)]["detection_recall"], 1.0)
        # plain ping never localizes; healthy network has no false positives
        for n in (1, 32):
            self.assertEqual(agg[("acl-drop", "Ping", n)]["localization_f1"], 0.0)
            for m in sweep.METHODS:
                self.assertEqual(agg[("none", m, n)]["false_positive_rate"], 0.0)

    def test_coverage_grows_for_host_probes(self):
        rows = []
        for seed in range(1, 6):
            rows += sweep.run_task(task("fat-tree", seed, max_exp=6, targets=("furthest",)))[0]
        agg = {(r["failure"], r["method"], r["probes_per_task"]): r for r in report.aggregate(rows)}
        cov = [agg[("none", "Ping", n)]["path_coverage_pct"] for n in (1, 4, 16, 64)]
        self.assertEqual(cov, sorted(cov))
        self.assertAlmostEqual(cov[0], 100.0 / 16, places=2)        # one ping, one of 16 paths
        self.assertEqual(agg[("none", "OPP_Sample_10", 1)]["path_coverage_pct"], 100.0)

    def test_parallel_matches_serial(self):
        tasks = [task("3d-torus", s, max_exp=2) for s in (1, 2)] + \
                [task("dragonfly", s, max_exp=2) for s in (1, 2)]
        serial = sweep.run_all(tasks, jobs=1)
        parallel = sweep.run_all(tasks, jobs=2)
        self.assertEqual([r for rows, _ in serial for r in rows],
                         [r for rows, _ in parallel for r in rows])


if __name__ == "__main__":
    unittest.main()
