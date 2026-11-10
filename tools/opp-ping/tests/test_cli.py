import contextlib
import io
import unittest

from helpers import topology  # noqa: F401  (sets sys.path)
from opp_ping import cli


def call(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


class Cli(unittest.TestCase):
    def test_info_all(self):
        rc, out = call("info")
        self.assertEqual(rc, 0)
        for title in ("Fat-tree (k=4)", "Leaf-spine (k=4)", "2D torus (n=4)", "3D torus (n=3)"):
            self.assertIn(title, out)

    def test_info_paper_numbers(self):
        rc, out = call("info", "--arch", "fat-tree", "--size", "64")
        self.assertIn("4426 entries", out)
        self.assertIn("1024 between host-0", out)
        self.assertIn("181.5 KB", out)

    def test_hosts_and_failures(self):
        rc, out = call("hosts", "--arch", "2d-torus", "--limit", "3")
        self.assertIn("11.0.0.1", out)
        self.assertIn("13 more", out)
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
            call("ping", "11.0.99.1")             # no such host in fat-tree k=4
        with self.assertRaises(SystemExit):
            call("ping", "11.0.16.1")             # node 16 is a switch
        with self.assertRaises(SystemExit):
            call("ping", "11.0.0.1", "--dst", "11.0.0.1")

    def test_demo(self):
        rc, out = call("demo", "--probes", "2", "--baseline", "4", "--seed", "9")
        self.assertEqual(rc, 0)
        self.assertEqual(out.count("[loop (native only)]"), 4)
        self.assertIn("3D torus", out)


if __name__ == "__main__":
    unittest.main()
