# opp-ping: OmniPath Ping from the command line

`opp-ping` is a ping-style tool for exploring OmniPath Ping (OPP) on the four
architectures from the paper, plus an OT plant network with no redundancy
(`ot-purdue`, see [below](#the-ot-network-ot-purdue)). You give it a source host IP. It
probes the **furthest** host from that source (or the **closest** with
`--target closest`), choosing at random among equally distant hosts,
optionally with an injected failure, and prints what OPP reports. It can also
run the paper's host-based baselines on the same flow for comparison.

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
tools/opp-ping/opp-ping ping 11.0.0.1              # fat-tree k=8, furthest host, healthy
tools/opp-ping/opp-ping ping 11.0.0.1 --failure congestion -c 5
tools/opp-ping/opp-ping ping 11.0.0.1 --failure loop --usm -c 5
tools/opp-ping/opp-ping ping 11.0.0.1 --failure acl-drop --baseline 64
tools/opp-ping/opp-ping ping 11.0.0.1 --arch 3d-torus --failure link-flap -c 20
tools/opp-ping/opp-ping ping 11.0.0.1 --failure port-down -c 1 --trace   # switch-by-switch log
tools/opp-ping/opp-ping ping 11.0.11.1 --arch ot-purdue --target closest --failure port-down
tools/opp-ping/opp-ping ping 11.0.11.1 --arch ot-purdue --failure loop --usm -c 5
tools/opp-ping/opp-ping demo                       # every failure, every architecture, both targets + CSV
tools/opp-ping/opp-ping demo --arch ot-purdue      # just the OT network
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
| `demo` | For each architecture and for both targets (furthest and closest), shows the target's IP, route and traversal, then runs every failure type (loop twice: with USM and with native multicast only) and prints OPP's results next to the baselines. `--target furthest` or `--target closest` limits it to one; `--src IP` changes the source. |

### Choosing the architecture

Use `--arch fat-tree|leaf-spine|2d-torus|3d-torus|ot-purdue` and `--size`:

- `--size` is **k** for fat-tree and leaf-spine (radix-k switches).
- `--size` is **n** for an n×n or n×n×n torus.
- `--size` is the **chain length n** for `ot-purdue`.
- **Every model has at least 50 hosts and 50 switches.** Smaller sizes are
  rejected with a message naming the smallest allowed size.
- The defaults are the smallest sizes that meet that floor:

  | Architecture | Default | Hosts | Switches |
  |---|---|---|---|
  | fat-tree | k=8 | 128 | 80 |
  | leaf-spine | k=34 | 578 | 51 |
  | 2d-torus | 8×8 | 64 | 64 |
  | 3d-torus | 4×4×4 | 64 | 64 |
  | ot-purdue | chains of 12 | 52 | 51 |
- Paper scale works too, e.g. `--size 64` for the 65,536-host fat-tree. One
  probe there takes about a second.

### Choosing the destination: furthest or closest

`--target furthest` (the default) or `--target closest` picks the destination
like this:

1. The tool computes hop distances from the source over the switch fabric.
2. It keeps every host at the maximum (furthest) or minimum (closest) distance.
3. It picks one of them at random.

For example:

- **Fat-tree k=8:** host-0 has 112 equally far hosts (every host outside its
  pod) and 3 closest hosts (the others on its ToR).
- **OT network, from 11.0.11.1:** the furthest devices are 11.0.39.1 and
  11.0.51.1, and the closest are 11.0.10.1 and 11.0.12.1.

`--dst IP` overrides the choice. The seed is always printed; rerun with
`--seed N` to get the same destination, failure placement and outcome.

Every `ping` and `demo` run prints the chosen target with its actual IP, the
other candidate IPs it was picked from, and how to get there:

```
target           : 11.0.39.1 dev-2a-12 is the furthest device: 23 switch hops away, picked at random among 2 equally far hosts (11.0.39.1, 11.0.51.1)
route            : 11.0.11.1 dev-1a-8 -> 1a-s8 -> 1a-s7 -> ... -> 1a-s1 -> area-1 -> core -> area-2 -> 2a-s1 -> ... -> 2a-s12 -> dev-2a-12 11.0.39.1
traverse         : 23 switch hops: up the cell-1a chain 1a-s8 .. 1a-s1 (8 switches) -> area-1 -> core; down area-2 -> the cell-2a chain 2a-s1 .. 2a-s12 (12 switches)
```

How to read these lines:

- **`route`** lists every hop. Where several equal-cost switches can be used,
  the hop shows them as a set, e.g. `{agg-p0-0|agg-p0-1}`.
- **`traverse`** summarizes the same route in words: the up and down parts in
  a Clos or OT network, or the moves per axis in a torus (for example,
  `x 2 steps (either direction, wrap-around), y +1`). Long runs along an OT
  daisy chain are shortened to `1a-s8 .. 1a-s1 (8 switches)`.

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
- **OT network:** a different node on purpose: the destination-side area
  switch's downlink into the target's cell.
- **Loops:** a switch that has an off-path neighbor to misroute to.
- **Tori:** a multi-path switch.
- **Closest targets on the same switch:** the switch's link to the target.

When a failure is injected, a `blast radius` line lists the hosts that would
lose contact with the source if that link went down completely.

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

## The OT network (`ot-purdue`)

This is a Purdue-model plant network. It is not in the paper; it was added to
see how OPP behaves without redundancy. It follows a fixed pattern:
**1 core → 2 areas → 2 cells each → a daisy chain of n switches per cell**.
Every switch has exactly one uplink, so the network is a tree.

```
  Level 3                          core ---- historian, eng-ws
                                 /      \
  Level 2     hmi-1 ---- area-1            area-2 ---- hmi-2
                         /      \          /      \
  Level 1/0          1a-s1    1b-s1     2a-s1    2b-s1     <- one field device
                       |        |         |        |          (dev-<cell>-<i>)
                     1a-s2    1b-s2     2a-s2    2b-s2        on every chain switch
                       :        :         :        :
                     1a-s12   1b-s12    2a-s12   2b-s12
```

| IPs | Devices | Attached to |
|---|---|---|
| 11.0.0.1, 11.0.1.1 | historian, eng-ws | core |
| 11.0.2.1, 11.0.3.1 | hmi-1, hmi-2 | area-1, area-2 |
| 11.0.4.1 – 11.0.15.1 | dev-1a-1 .. dev-1a-12 | 1a-s1 .. 1a-s12 |
| 11.0.16.1 – 11.0.27.1 | dev-1b-1 .. dev-1b-12 | 1b-s1 .. 1b-s12 |
| 11.0.28.1 – 11.0.39.1 | dev-2a-1 .. dev-2a-12 | 2a-s1 .. 2a-s12 |
| 11.0.40.1 – 11.0.51.1 | dev-2b-1 .. dev-2b-12 | 2b-s1 .. 2b-s12 |

With chains of 12 the network has 52 hosts, 51 switches and 102 links (4 + 4n
hosts, 3 + 4n switches). That is the smallest chain length that meets the
50 + 50 floor.

**The OT tests start from 11.0.11.1** rather than 11.0.0.1. With chains of 12
that is dev-1a-8, low in cell 1a's daisy chain. `demo` and `info` use that
source by default too.

From there, the closest devices are its chain neighbors 11.0.10.1 and
11.0.12.1 (2 switch hops). The furthest are the ends of the area-2 chains,
11.0.39.1 and 11.0.51.1 (23 switch hops).

**What to expect:**

- **Coverage is trivial.** There is exactly one path between any two devices,
  so OPP never duplicates probes and path coverage is always 1/1.
- **Every link is a single point of failure.** The worst single outage, a
  core – area link, cuts off 25 hosts.
- **Traceroute has no path ambiguity here.** OPP's remaining advantage is
  localizing a failure with one probe instead of a TTL sweep.
- **Loops still need USM to be seen.**

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

## Demo CSV (for graphs)

`demo` always writes a tidy, long-format CSV next to its printed output, to
`opp_ping_demo_seed<SEED>.csv` in the current directory. `--csv PATH` picks
another file and `--no-csv` turns it off.

There is one row per **topology × target (furthest/closest) × injected failure
× method**. A default run has 5 × 2 × 7 cases, which gives 250 rows. The
"native multicast only" loop case has an OPP row only, because the baselines
don't depend on OPP's multicast mode.

Each column belongs to one of these groups:

| Group | Columns |
|---|---|
| Run and topology | `seed`, `architecture`, `size`, `hosts`, `switches`, `links` |
| Flow | `source_ip`, `source_device`, `target` (furthest/closest), `target_ip`, `target_device`, `switch_hops`, `equal_cost_paths` |
| Injected issue | `failure` (none, acl-drop, loop, link-flap, congestion, port-down), `failure_category`, `variant` (default, or usm/native for loops), `usm_rate_pct`, `injected_link`, `blast_radius_hosts` |
| Method | `method`: OPP, R-Pingmesh (Ping + Traceroute), Ping (plain), RD-Probe (Ping + mirroring) |
| Outcome | `probes_sent`, `detected`, `localized`, `false_positive` (healthy runs only), `detection_rate`, `localization_rate`, `first_detection_at`, `first_localization_at`, `missed_probes`, `wrong_reports` |
| Cost and quality | `path_coverage_pct`, `full_coverage_rate`, `avg_rtt_us`, `max_rtt_us`, `switch_packets_per_probe`, `recirculations_per_probe` |
| Text | `diagnosis` |

What the outcome columns mean:

- **`detected` and `localized`** are 0/1 for the whole case.
- **The `_rate` columns** are per probe.
- **For R-Pingmesh, `probes_sent`** includes the Traceroute probes.
- **Ping never localizes,** so its `localized` is always 0.
- **Blank cells** mean "not applicable".

For example, with pandas:

```python
import pandas as pd
df = pd.read_csv("opp_ping_demo_seed21.csv")
faults = df[(df.failure != "none") & (df.variant != "native")]
faults.pivot_table(index=["architecture", "target"], columns="method",
                   values="localized", aggfunc="mean")
```

## Tests

```bash
cd tools/opp-ping
python3 -m unittest discover -s tests -v          # 80 tests, about 3 s
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
- **The 50-host / 50-switch floor:** defaults meet it, smaller sizes are
  rejected, and the host and switch count formulas are checked.
- **The demo CSV:** 250 rows with the expected columns, every
  topology × target × failure × method combination present, and at least
  50 hosts and 50 switches on every row.

`tests/test_ot.py` adds the OT network checks:

- **Shape:** the 1 → 2 → 4 → chains pattern, one uplink per switch, every link
  a bridge, one path between every pair of hosts, and the IP plan.
- **Targets from 11.0.11.1:** the closest set {11.0.10.1, 11.0.12.1} and the
  furthest set {11.0.39.1, 11.0.51.1}, with random tie-breaking reaching both,
  plus the exact `route` and `traverse` text.
- **Placement:** default failures go on a different node than in the Clos
  tests.
- **Every link on the path:** ACL drop, port down and congestion injected on
  each link of the path to every closest and furthest device, each localized
  by one probe, in both multicast modes.
- **Link flap and loops:** link flap is detected and never mislocalized; loops
  need USM.
- **Blast radius values** and the worst outage.
- **Traceroute** shows one switch per TTL (no ambiguity).
- **CLI output:** it shows the furthest/closest labels, the IPs, the route and
  the traversal.

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
