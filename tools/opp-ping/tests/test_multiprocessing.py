"""The demo's worker pool must work with every multiprocessing start method.

Python 3.14 changed the Linux default from "fork" to "forkserver", which
re-imports the main script in each worker; an unguarded launcher then reran
the whole demo in every worker and crashed. These tests force each method.
"""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAUNCHER = os.path.join(os.path.dirname(HERE), "opp-ping")

SCRIPT = """
import multiprocessing, runpy, sys
multiprocessing.set_start_method(%r)
sys.argv = ["opp-ping", "demo", "--arch", "dragonfly", "--runs", "3", "--max-exp", "0",
            "--jobs", "2", "--seed", "11", "--csv", %r]
runpy.run_path(%r, run_name="__main__")
"""


class StartMethods(unittest.TestCase):
    def run_demo(self, method, csv_path):
        out = subprocess.run([sys.executable, "-c", SCRIPT % (method, csv_path, LAUNCHER)],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=300)
        return out.returncode, out.stdout

    def test_every_start_method_gives_the_same_csv(self):
        import multiprocessing
        csvs = {}
        with tempfile.TemporaryDirectory() as tmp:
            for method in multiprocessing.get_all_start_methods():
                path = os.path.join(tmp, "%s.csv" % method)
                with self.subTest(method=method):
                    rc, out = self.run_demo(method, path)
                    self.assertEqual(rc, 0, out)
                    self.assertEqual(out.count("opp-ping demo:"), 1, out)   # not rerun by workers
                    self.assertNotIn("Error", out)
                    with open(path) as f:
                        csvs[method] = f.read()
        self.assertEqual(len(set(csvs.values())), 1, sorted(csvs))

    def test_launcher_does_nothing_when_imported(self):
        # this is exactly what a spawn/forkserver worker does with the main script
        out = subprocess.run([sys.executable, "-c",
                              "import runpy; runpy.run_path(%r, run_name='__mp_main__'); "
                              "print('imported fine')" % LAUNCHER],
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(out.stdout.strip(), "imported fine")


if __name__ == "__main__":
    unittest.main()
