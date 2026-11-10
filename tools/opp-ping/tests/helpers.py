import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from opp_ping import baseline as baseline_mod, engine, failures, routing, session, topology  # noqa: E402,F401

ARCHES = list(topology.ARCHES)


def setup(arch, size=None, seed=1, kind="none", **params):
    """Build a topology, pick the furthest destination from host 0 and place a failure."""
    topo = topology.build(arch, size)
    rt = routing.Routing(topo)
    rng = random.Random(seed)
    dst, _, _ = rt.pick_furthest(0, rng)
    fail = failures.place(kind, topo, rt, 0, dst, rng, **params)
    return topo, rt, rng, 0, dst, fail


def run(arch, kind="none", count=1, usm_rate=1.0, seed=1, size=None, **params):
    topo, rt, rng, src, dst, fail = setup(arch, size, seed, kind, **params)
    sess = session.Session(topo, rt, fail, rng, src, dst)
    return topo, rt, fail, sess.run(count, usm_rate)
