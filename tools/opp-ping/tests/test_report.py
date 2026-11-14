"""Averaging of per-run demo rows into the CSV."""

import os
import tempfile
import unittest

from helpers import topology  # noqa: F401  (sets sys.path)
from opp_ping import report


def row(**kw):
    base = {c: "" for c in report.CONSTANT}
    base.update({"architecture": "fat-tree", "target": "furthest", "failure": "acl-drop",
                 "variant": "default", "method": "OPP", "target_ip": "11.0.1.1",
                 "injected_link": "a -> b"})
    base.update(kw)
    return base


class Aggregate(unittest.TestCase):
    def test_means_fractions_and_blanks(self):
        rows = [row(detected=1, localized=1, first_detection_at=2, probes_sent=10),
                row(detected=0, localized=0, first_detection_at="", probes_sent=12,
                    target_ip="11.0.2.1"),
                row(detected=1, localized=0, first_detection_at=4, probes_sent=14,
                    injected_link="c -> d")]
        out = report.aggregate(rows)
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual(r["runs"], 3)
        self.assertAlmostEqual(r["detected"], 2 / 3.0, places=4)   # fraction of runs
        self.assertAlmostEqual(r["localized"], 1 / 3.0, places=4)
        self.assertEqual(r["first_detection_at"], 3.0)             # only runs that detected
        self.assertEqual(r["probes_sent"], 12.0)
        self.assertEqual(r["distinct_targets"], 2)
        self.assertEqual(r["distinct_injected_links"], 2)
        self.assertEqual(r["false_positive"], "")                  # never had a value

    def test_one_row_per_key_in_first_seen_order(self):
        rows = [row(method="OPP"), row(method="Ping"), row(method="OPP"),
                row(method="OPP", target="closest")]
        out = report.aggregate(rows)
        self.assertEqual([(r["method"], r["target"], r["runs"]) for r in out],
                         [("OPP", "furthest", 2), ("Ping", "furthest", 1), ("OPP", "closest", 1)])

    def test_write_overwrites(self):
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        try:
            report.write_csv(path, report.aggregate([row(), row()]))
            report.write_csv(path, report.aggregate([row(method="Ping")]))
            with open(path) as f:
                lines = f.read().splitlines()
        finally:
            os.unlink(path)
        self.assertEqual(lines[0], ",".join(report.COLUMNS))
        self.assertEqual(len(lines), 2)
        self.assertIn(",Ping,1,", lines[1])
        self.assertNotIn("seed", lines[0])


if __name__ == "__main__":
    unittest.main()
