"""Run a sequence of OPP probes for one probe task and judge the results
against the injected failure."""

import math

from . import engine

EXPECTED_KIND = {
    "acl-drop": "loss",
    "link-flap": "loss",
    "port-down": "loss",
    "congestion": "congestion",
    "loop": "loop",
}


def usm_schedule(count, usm_rate, rng):
    """Round-robin USM sampling (Section 5.2): one USM probe every ceil(1/p)."""
    if usm_rate >= 1.0:
        return [True] * count
    if usm_rate <= 0.0:
        return [False] * count
    period = int(math.ceil(1.0 / usm_rate))
    offset = rng.randrange(period)
    return [((i + offset) % period) == 0 for i in range(count)]


class Session(object):
    def __init__(self, topo, routing, failure, rng, src, dst, trace=False):
        self.topo, self.routing, self.failure = topo, routing, failure
        self.rng, self.src, self.dst = rng, src, dst
        self.net = engine.OppNetwork(topo, routing, failure, rng, trace=trace)
        self.next_seq = 1
        self.results = []

    def run(self, count, usm_rate=0.1, interval_ns=10 * 1000 * 1000):
        schedule = usm_schedule(count, usm_rate, self.rng)
        start = self.net.now
        for i, usm in enumerate(schedule):
            self.net.now = max(self.net.now, start + i * interval_ns)
            res = self.net.probe(self.src, self.dst, self.next_seq, usm=usm)
            res.index = i + 1
            self.next_seq += 1
            res.findings = engine.diagnose(self.topo, self.routing, res)
            if any(k in ("loop", "misroute") for k, _, _ in res.findings):
                self.next_seq = engine.localize_loop(self.net, res, self.next_seq)
            res.verdict = judge(self.failure, res)
            self.results.append(res)
        return self.results


def judge(failure, res):
    """'ok' | 'detected' | 'missed' | 'wrong' | 'false-positive' for one probe."""
    kinds = {k for k, _, _ in res.findings}
    if failure.kind == "none":
        return "false-positive" if res.findings else "ok"
    if not res.findings:
        return "missed"
    if failure.kind == "loop":
        if res.misconfigured_switch == failure.switch:
            return "detected"
        return "wrong" if res.misconfigured_switch is not None else "partial"
    want = EXPECTED_KIND[failure.kind]
    for kind, link, _ in res.findings:
        if kind == want and link == failure.link:
            return "detected"
    return "partial" if want in kinds else "wrong"


def tally(results):
    out = {}
    for r in results:
        out[r.verdict] = out.get(r.verdict, 0) + 1
    return out
