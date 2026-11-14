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
                      "OT Purdue plant (n=12)", "Dragonfly (a=4", "Dragonfly+ ("):
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
            rc, out = call("demo", "--runs", "3", "--probes", "2", "--baseline", "4",
                           "--seed", "4", "--csv", path)
            self.assertIn("wrote 350 rows to", out)
            self.assertIn("averaged over 3 run(s)", out)
            with open(path) as f:
                rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 350)
        self.assertEqual(list(rows[0]), report.COLUMNS)
        self.assertNotIn("seed", rows[0])
        self.assertTrue(all(r["runs"] == "3" for r in rows))
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
        self.assertEqual(len(combos), 7 * 2 * 7)
        for key, methods in combos.items():
            want = {"OPP"} if key[3] == "native" else set(report.METHODS)
            self.assertEqual(methods, want, key)
        for r in rows:
            self.assertGreaterEqual(int(r["hosts"]), 50)
            self.assertGreaterEqual(int(r["switches"]), 50)
            if r["detected"] != "":
                self.assertTrue(0.0 <= float(r["detected"]) <= 1.0)

    def test_demo_overwrites_default_csv(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                with open(cli.DEFAULT_CSV, "w") as f:
                    f.write("stale,data\n1,2\n")
                call("demo", "--arch", "3d-torus", "--runs", "2", "--probes", "1",
                     "--baseline", "2", "--seed", "5")
                with open(cli.DEFAULT_CSV) as f:
                    first = f.read()
                call("demo", "--arch", "3d-torus", "--runs", "2", "--probes", "1",
                     "--baseline", "2", "--seed", "6")
                with open(cli.DEFAULT_CSV) as f:
                    second = f.read()
                files = sorted(os.listdir(tmp))
            finally:
                os.chdir(old)
        self.assertEqual(files, [cli.DEFAULT_CSV])            # one file, no per-seed copies
        self.assertNotIn("stale", first)
        self.assertTrue(first.startswith(",".join(report.COLUMNS)))
        self.assertEqual(len(second.splitlines()), 1 + 2 * 7 * 4 - 2 * 3)   # rewritten, not appended

    def test_demo_no_csv(self):
        rc, out = call("demo", "--arch", "3d-torus", "--runs", "1", "--probes", "1",
                       "--baseline", "0", "--seed", "1", "--no-csv")
        self.assertNotIn("wrote", out)

    def test_demo_mode(self):
        parse = cli.build_parser().parse_args
        self.assertEqual(cli.demo_mode(parse(["demo"])), (cli.FULL_DEMO_RUNS, False))
        self.assertEqual(cli.FULL_DEMO_RUNS, 50)
        for narrowing in (["--target", "closest"], ["--target", "furthest"],
                          ["--arch", "dragonfly"], ["--src", "11.0.3.1"], ["--runs", "1"]):
            self.assertEqual(cli.demo_mode(parse(["demo"] + narrowing)), (1, True), narrowing)
        self.assertEqual(cli.demo_mode(parse(["demo", "--target", "closest", "--runs", "5"])),
                         (5, False))

    def test_full_demo_prints_only_averaged_results(self):
        rc, out = call("demo", "--runs", "2", "--probes", "2", "--baseline", "4", "--seed", "9",
                       "--no-csv")
        self.assertEqual(rc, 0)
        self.assertNotIn("[loop (native only) -> ", out)      # no per-case output
        self.assertNotIn("route            :", out)
        self.assertEqual(out.count("-- averaged over 2 runs"), 7)
        self.assertEqual(out.count("loop (native only)"), 7)
        self.assertIn("overall: fraction of failure cases localized, averaged over 2 runs", out)
        self.assertIn("Dragonfly+", out)

    def test_individual_run_output(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                rc, out = call("demo", "--target", "closest", "--arch", "ot-purdue",
                               "--probes", "2", "--baseline", "4", "--seed", "3")
                files = os.listdir(tmp)
                call("demo", "--target", "furthest", "--arch", "fat-tree", "--probes", "1",
                     "--baseline", "0", "--seed", "3", "--csv", "one.csv")
                files_after = sorted(os.listdir(tmp))
            finally:
                os.chdir(old)
        self.assertIn("individual run", out)
        self.assertIn("--- CLOSEST device from 11.0.11.1:", out)
        self.assertNotIn("FURTHEST device", out)
        self.assertEqual(out.count("[loop (native only) -> closest]"), 1)
        self.assertIn("this run", out)
        self.assertEqual(files, [])                     # no CSV unless asked
        self.assertEqual(files_after, ["one.csv"])      # written when --csv is given

if __name__ == "__main__":
    unittest.main()
