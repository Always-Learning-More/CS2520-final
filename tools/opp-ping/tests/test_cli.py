import contextlib
import io
import unittest

from helpers import topology  # noqa: F401  (sets sys.path)
from opp_ping import cli, report


def call(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


class Cli(unittest.TestCase):
    def test_info_all(self):
        rc, out = call("info")
        self.assertEqual(rc, 0)
        for title in ("Fat-tree (k=8)", "Leaf-spine (k=34)", "2D torus (n=8)", "3D torus (n=4)",
                      "OT Purdue plant (n=12)"):
            self.assertIn(title, out)

    def test_info_paper_numbers(self):
        rc, out = call("info", "--arch", "fat-tree", "--size", "64")
        self.assertIn("4426 entries", out)
        self.assertIn("1024 to the furthest host", out)
        self.assertIn("181.5 KB", out)

    def test_hosts_and_failures(self):
        rc, out = call("hosts", "--arch", "2d-torus", "--limit", "3")
        self.assertIn("11.0.0.1", out)
        self.assertIn("61 more", out)                     # 8x8 torus = 64 hosts
        rc, out = call("failures")
        for kind in ("acl-drop", "loop", "link-flap", "congestion", "port-down"):
            self.assertIn(kind, out)

    def test_ping_every_failure_every_arch(self):
        for arch in cli.ARCH_CHOICES:
            for kind in ("none", "acl-drop", "loop", "link-flap", "congestion", "port-down"):
                with self.subTest(arch=arch, kind=kind):
                    rc, out = call("ping", "11.0.0.1", "--arch", arch, "--failure", kind,
                                   "-c", "3", "--usm", "--seed", "3")
                    self.assertEqual(rc, 0)
                    self.assertIn("OPP PING 11.0.0.1", out)
                    self.assertIn("furthest device", out)
                    self.assertIn("opp-ping statistics", out)

    def test_ping_trace_and_baseline(self):
        rc, out = call("ping", "11.0.1.1", "--failure", "congestion", "-c", "1",
                       "--trace", "--baseline", "16", "--seed", "1")
        self.assertIn("new entry", out)
        self.assertIn("CONGESTION", out)
        self.assertIn("R-Pingmesh*", out)
        self.assertIn("RD-Probe*", out)

    def test_ping_explicit_destination(self):
        rc, out = call("ping", "host-0", "--dst", "11.0.1.1", "-c", "1", "--seed", "1")
        self.assertIn("given with --dst", out)

    def test_bad_addresses(self):
        with self.assertRaises(SystemExit):
            call("ping", "11.1.200.1")            # node 456: no such node in fat-tree k=8
        with self.assertRaises(SystemExit):
            call("ping", "11.0.128.1")            # node 128 is a switch in fat-tree k=8
        with self.assertRaises(SystemExit):
            call("ping", "11.0.0.1", "--dst", "11.0.0.1")

    def test_small_size_rejected(self):
        with self.assertRaises(SystemExit) as cm:
            call("ping", "11.0.0.1", "--size", "4")
        self.assertIn("at least 50 hosts and 50 switches", str(cm.exception))
        with self.assertRaises(SystemExit):
            call("info", "--arch", "ot-purdue", "--size", "3")

    def test_demo_csv(self):
        import csv
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.csv")
            rc, out = call("demo", "--probes", "2", "--baseline", "4", "--seed", "4", "--csv", path)
            self.assertIn("wrote 250 rows to", out)
            with open(path) as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 250)
        self.assertEqual(list(rows[0]), report.COLUMNS)
        self.assertEqual({r["architecture"] for r in rows}, set(cli.ARCH_CHOICES))
        self.assertEqual({r["target"] for r in rows}, {"furthest", "closest"})
        self.assertEqual({r["method"] for r in rows}, set(report.METHODS))
        self.assertEqual({r["failure"] for r in rows},
                         {"none", "acl-drop", "loop", "link-flap", "congestion", "port-down"})
        # every (arch, target, failure, variant) has OPP; baselines for all but native-only loops
        combos = {}
        for r in rows:
            combos.setdefault((r["architecture"], r["target"], r["failure"], r["variant"]),
                              set()).add(r["method"])
        self.assertEqual(len(combos), 5 * 2 * 7)
        for key, methods in combos.items():
            want = {"OPP"} if key[3] == "native" else set(report.METHODS)
            self.assertEqual(methods, want, key)
        for r in rows:
            self.assertGreaterEqual(int(r["hosts"]), 50)
            self.assertGreaterEqual(int(r["switches"]), 50)

    def test_demo_no_csv(self):
        rc, out = call("demo", "--arch", "3d-torus", "--probes", "1", "--baseline", "0",
                       "--seed", "1", "--no-csv")
        self.assertNotIn("wrote", out)

    def test_demo(self):
        rc, out = call("demo", "--probes", "2", "--baseline", "4", "--seed", "9", "--no-csv")
        self.assertEqual(rc, 0)
        # 5 architectures x (furthest, closest)
        self.assertEqual(out.count("[loop (native only) -> "), 10)
        self.assertIn("3D torus", out)
        self.assertIn("OT Purdue plant", out)


if __name__ == "__main__":
    unittest.main()
