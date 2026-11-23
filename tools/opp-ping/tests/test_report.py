"""Averaging of per-run, per-budget demo rows into the CSV."""

import os
import tempfile
import unittest

from helpers import topology  # noqa: F401  (sets sys.path)
from opp_ping import report


def row(**kw):
    base = {c: "" for c in report.CONSTANT}
    base.update({"architecture": "fat-tree", "target": "furthest", "failure": "acl-drop",
                 "method": "OPP_Sample_10", "probes_per_task": 4, "target_ip": "11.0.1.1",
                 "injected_link": "a -> b", "detected": 1, "localized": 1, "wrong": 0,
                 "false_positive": "", "probes_sent": 4})
    base.update(kw)
    return base


class Aggregate(unittest.TestCase):
    def test_recall_and_f1(self):
        rows = [row(),                                            # TP
                row(localized=0, wrong=1),                        # FP + FN
                row(detected=0, localized=0),                     # FN
                row(target_ip="11.0.2.1", injected_link="c -> d")]  # TP
        out = report.aggregate(rows)
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual(r["runs"], 4)
        self.assertEqual(r["detection_recall"], 0.75)             # Figure 8
        self.assertEqual(r["localization_tp_rate"], 0.5)
        self.assertEqual(r["localization_fp_rate"], 0.25)
        # TP=2, FP=1, FN=2 -> F1 = 4 / (4 + 1 + 2)
        self.assertAlmostEqual(r["localization_f1"], 4 / 7.0, places=4)   # Figure 9
        self.assertEqual(r["false_positive_rate"], "")
        self.assertEqual(r["distinct_targets"], 2)
        self.assertEqual(r["distinct_injected_links"], 2)
        self.assertEqual(r["probes_sent"], 4.0)

    def test_healthy_rows(self):
        rows = [row(failure="none", detected="", localized="", wrong="", false_positive=0),
                row(failure="none", detected="", localized="", wrong="", false_positive=1)]
        r = report.aggregate(rows)[0]
        self.assertEqual(r["false_positive_rate"], 0.5)
        self.assertEqual(r["detection_recall"], "")
        self.assertEqual(r["localization_f1"], "")

    def test_one_row_per_budget_and_method(self):
        rows = [row(probes_per_task=1), row(probes_per_task=2), row(probes_per_task=1),
                row(method="RD-Probe", probes_per_task=1)]
        out = report.aggregate(rows)
        self.assertEqual([(r["method"], r["probes_per_task"], r["runs"]) for r in out],
                         [("OPP_Sample_10", 1, 2), ("OPP_Sample_10", 2, 1), ("RD-Probe", 1, 1)])

    def test_probes_needed(self):
        rows = report.aggregate([row(probes_per_task=1, localized=0, detected=0),
                                 row(probes_per_task=2), row(probes_per_task=4),
                                 row(method="Ping", probes_per_task=4, localized=0)])
        need = report.probes_needed(rows, "localization_f1", 0.95)
        self.assertEqual(need[("fat-tree", "furthest", "acl-drop", "OPP_Sample_10")], 2)
        self.assertIsNone(need[("fat-tree", "furthest", "acl-drop", "Ping")])

    def test_write_overwrites(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            report.write_csv(path, report.aggregate([row(), row(probes_per_task=8)]))
            report.write_csv(path, report.aggregate([row(method="Ping")]))
            with open(path) as f:
                lines = f.read().splitlines()
        finally:
            os.unlink(path)
        self.assertEqual(lines[0], ",".join(report.COLUMNS))
        self.assertEqual(len(lines), 2)
        self.assertIn(",Ping,", lines[1])
        self.assertNotIn("seed", lines[0])


if __name__ == "__main__":
    unittest.main()
