# opp-ping: OmniPath Ping from the command line

`opp-ping` is a ping-style tool for exploring OmniPath Ping (OPP) on the four
architectures from the paper. You give it a source host IP. By default it
probes the **furthest** host from that source, choosing at random among equally
far hosts, optionally with an injected failure, and prints what OPP reports.
It can also run the paper's host-based baselines on the same flow for
comparison.

It is a pure-Python model of the data plane described in Section 4 and
Appendix B of the paper:

- the OP cache
- probe duplication and deduplication
- unicast-based simulated multicast (USM) versus native multicast
- ACK aggregation
- entry expiry
- loop detection and two-phase loop localization

It runs instantly with only the Python standard library, so it needs no
container, Python 2, or ns-3 build. It is **not** the ns-3 simulator (see
[Fidelity](#fidelity)). Use the `experiment_reproduction/` runners for the
paper's numbers.

Topologies, node ids, port numbers and IP addresses match the repo's ns-3
topology files exactly; the tests check this link by link. As in the
simulator, a host's IP is `11.(id // 256).(id % 256).1`.

## Quick start

Run these from the repository root.

```bash
tools/opp-ping/opp-ping info                       # all four architectures
tools/opp-ping/opp-ping ping 11.0.0.1              # fat-tree k=4, furthest host, healthy
tools/opp-ping/opp-ping ping 11.0.0.1 --failure congestion -c 5
tools/opp-ping/opp-ping ping 11.0.0.1 --failure loop --usm -c 5
tools/opp-ping/opp-ping ping 11.0.0.1 --failure acl-drop --baseline 64
tools/opp-ping/opp-ping ping 11.0.0.1 --arch 3d-torus --failure link-flap -c 20
tools/opp-ping/opp-ping ping 11.0.0.1 --failure port-down -c 1 --trace   # switch-by-switch log
tools/opp-ping/opp-ping demo                       # every failure on every architecture
```

To call it as just `opp-ping`, add it to your PATH:

```bash
ln -s "$PWD/tools/opp-ping/opp-ping" ~/.local/bin/opp-ping
```

## Commands

| Command | What it does |
|---|---|
| `ping IP` | Runs OPP probes from host `IP` to the furthest host (or `--dst`), then prints one line per probe and a statistics block. |
| `info` | Shows general information per architecture: hosts and switches per tier, links, IP range, diameter, equal-cost paths, ECMP fan-out, base RTT, OPP task grouping, and OP cache size per switch from the paper's formulas. |
| `hosts` | Lists host IPs with the switch each host is attached to and its position. |
| `failures` | Lists the failure types. |
| `demo` | For each architecture, runs every failure type (loop twice: with USM and with native multicast only) and prints OPP's results next to the baselines. |

### Choosing the architecture

Use `--arch fat-tree|leaf-spine|2d-torus|3d-torus` and `--size`:

- `--size` is **k** for fat-tree and leaf-spine (radix-k switches).
- `--size` is **n** for an n×n or n×n×n torus.
- Defaults are small so the output is readable: fat-tree k=4, leaf-spine k=4,
  2D torus 4×4, 3D torus 3×3×3.
- Paper scale works too, e.g. `--size 64` for the 65,536-host fat-tree. One
  probe there takes about a second.

### Choosing the destination

Without `--dst`, the destination is chosen like this:

1. The tool computes hop distances from the source over the switch fabric.
2. It keeps every host at the maximum distance.
3. It picks one of them at random.

For a fat-tree k=4, host-0 has 12 equally far hosts (every host outside its pod). The seed is always
printed; rerun with `--seed N` to get the same destination, failure placement
and outcome.

### Failures

| `--failure` | Category | Model |
|---|---|---|
| `acl-drop` | flow-level (Table 1 #8) | Drops every packet of the probed flow on one link. ACKs are the reverse flow and are not matched. |
| `loop` | flow-level (Table 1 #9) | The switch's **unicast** table sends packets meant for one port to an off-path neighbor with probability `--loop-prob` (default 50%, as in the paper). The native multicast table stays correct. |
| `link-flap` | device-level (Table 1 #1) | Drops `--flap-prob` (default 20%) of packets on the link, in both directions. |
| `congestion` | device-level (Table 1 #4) | Adds `--congestion-us` (default 500 µs) of queueing delay on the link. |
| `port-down` | device-level (Table 1 #2) | The link drops everything in both directions. |

By default the failure goes on the probed flow's paths, the way the paper sets
it up:

- **Fat-tree:** source-pod aggregation uplinks.
- **Leaf-spine:** source-leaf uplinks.
- **Loops:** a switch that has an off-path neighbor to misroute to.
- **Tori:** a multi-path switch.

The location is chosen at random among those candidates. You can override it:

- `--fail-at DEVICE[:PORT]`, e.g. `--fail-at agg-p0-1:3`
- `--fail-link A,B`, e.g. `--fail-link agg-p0-1,core-2`

Devices are given by name, node id or IP. A failure that is off the flow's
paths is flagged and stays invisible to the probe.

### USM versus native multicast

- `--usm-rate P` sets the percentage of probes that use USM (paper default 10%,
  round-robin, so there is one USM probe every ⌈100/P⌉ probes).
- `--usm` means every probe uses USM.
- `--native` means no probe does.

Only USM probes consult the unicast table, so only they can expose a loop
(paper Figure 8b).

## Reading the output

```
probe=1   USM    rtt=  506.0 us  paths 6/6  recirc 4  switch-pkts 26  -> CONGESTION sw-0-1-0 -> sw-1-1-0  [detected]
```

- **`mode`:** USM or native multicast. Loop phase-2 re-injections add `+auth`.
- **`rtt`:** time until the source host receives the single aggregated ACK.
  If a probe is lost, this includes the entry-expiry timer: about
  remaining-hops × 10 ms.
- **`paths x/y`:** the `Equal_Cost_Path_Count` returned in the ACK, out of the
  true number of equal-cost paths. It drops below `y` when a path is broken.
- **`recirc`:** USM recirculation passes across all switches. Native multicast
  always shows 0.
- **`switch-pkts`:** probes plus ACKs received by switches. With duplication
  and deduplication, each equal-cost link carries exactly one of each.
- **Reports** use the paper's agent rules (Section 4.6):
  - `LOSS link`: from `Faulty_Link`.
  - `CONGESTION link`: `Maximum_QDelay` above 100 µs.
  - `LOOP`: `Is_Loop=1`.
  - `MISROUTE link`: an unexpected link out of an on-path switch, which pins
    the misconfigured switch (Appendix C).
  - When a loop is seen without a misroute, the tool re-injects
    path-authorized probes (phase 2) to localize it.
- **`[verdict]`:** compares the report with the injected failure:
  - `detected` means detected *and* localized to the right link or switch.
  - `missed` means nothing was reported.
  - `partial` means the right kind of failure was reported at the wrong place.
  - `wrong` means a different kind of failure was reported.

`--trace` prints the event log: entry creation, multicast or USM ports,
deduplication, ACK aggregation, expiry and drops, with timestamps.

`--baseline N` sends N ordinary sprayed pings and, if they show an anomaly,
runs Traceroute with N probes per TTL. It then prints:

- packet loss, RTT, and paths covered, with the coupon-collector estimate of
  pings needed to cover every path;
- which switches answered at each TTL, which shows path ambiguity;
- the R-Pingmesh* (Traceroute heuristics) and RD-Probe* (mirroring) diagnoses.

## Tests

```bash
cd tools/opp-ping
python3 -m unittest discover -s tests -v          # 51 tests, about 2 s
```

The suite covers:

- **Topology parity** with every topology file in the repo, the IP scheme, and
  device lookup.
- **Furthest-host selection:** maximal distance, random tie-breaking, and
  reproducibility with a seed.
- **Equal-cost path counts:** for example, 1,024 paths for the k=64 fat-tree
  (paper Section 1).
- **One probe covers every path,** and each equal-cost link carries exactly one
  probe and one ACK.
- **USM recirculations** at k=64: roughly 130 per switch, matching paper
  Appendix E.2.
- **Single-probe localization** of ACL drop, port down and congestion, on every
  architecture, for several seeds and both multicast modes.
- **Link flapping:** detected over repeated probes and never mislocalized.
- **Loops:** invisible to native multicast, but found and pinned to the right
  switch by USM; USM sampling bounds the detection latency.
- **Edge cases:** loss on the host link (reported by the agent's local timer),
  failures off the flow's paths, and placement errors.
- **Baselines:** coverage, the coupon-collector estimate, the mirror baseline's
  localization, and Traceroute's path ambiguity.
- **Every CLI subcommand,** including error handling for bad or switch IPs.

The tests also run under `pytest`.

## Fidelity

These are the differences from the paper and the ns-3 model.

- **One probe task, run sequentially.** Each probe runs to completion before
  the next one, one probe period apart. There is no background traffic and no
  concurrency control between tasks: one Token_ID is used throughout.
- **Timing.**
  - Links take 1 µs each and serialize 100-byte probes at 400 Gbps.
  - A USM recirculation pass costs 700 ns.
  - Congestion is a static extra delay.
  - `T_expire` = 10 ms, `T_qdelay` = 100 µs and `T_recirc` = 1,000, as in the
    paper.
- **Two small rules the paper leaves implicit.**
  - A legitimate copy that arrives after a switch has already aggregated its
    ACKs is answered at once; otherwise a 500 µs-late copy would look like
    loss.
  - The host's local timer gets one extra `T_expire`, so the source ToR's
    expiry-generated ACK always arrives first.
- **Loop exposure depends on `--loop-prob`.** With the paper's 50%
  misrouting, each USM probe exposes the loop with probability of about 50%.
- **The baselines are simplified models** of R-Pingmesh* and RD-Probe*: one
  flow, and the same failure models.
