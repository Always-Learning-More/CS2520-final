# opp-ping: OmniPath Ping from the command line

`opp-ping` is a ping-style tool for exploring OmniPath Ping (OPP) on the four
architectures from the paper's ns-3 evaluation, the two Dragonfly variants
from its Table 6 ([below](#dragonfly-and-dragonfly)), and an OT plant network
with no redundancy (`ot-purdue`, see [below](#the-ot-network-ot-purdue)). You give it a source host IP. It
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
simulator, a host's IP is `11.(id // 256).(id % 256).1`; past id 65,535 the
simulator's 32-bit arithmetic rolls over into `12.X.Y.1`.

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
tools/opp-ping/opp-ping demo                       # 50 runs of every case: averaged results + CSV
tools/opp-ping/opp-ping demo --target closest      # one individual run, printed case by case
tools/opp-ping/opp-ping demo --arch ot-purdue      # just the OT network
tools/opp-ping/opp-ping ping 11.0.0.1 --arch dragonfly --failure port-down
tools/opp-ping/opp-ping info --arch dragonfly-plus --size paper   # the paper's 67,584-host size
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
| `demo` | Runs every failure type (loop twice: with USM and with native multicast only) on every architecture and both targets (furthest and closest). The full demo repeats this **50 times** with new random draws and prints **only the averaged results**: one table per architecture, then an overall table, plus the CSV. Narrowing it with `--target furthest\|closest`, `--arch`, `--src` or `--runs 1` turns it into an **individual run**: one run, printed case by case with target IPs, routes and traversals. |

### Choosing the architecture

Use `--arch fat-tree|leaf-spine|2d-torus|3d-torus|ot-purdue|dragonfly|dragonfly-plus`
and `--size`:

- `--size` is **k** for fat-tree and leaf-spine (radix-k switches).
- `--size` is **n** for an n×n or n×n×n torus.
- `--size` is the **chain length n** for `ot-purdue`.
- `--size` is the **switches per group a** for `dragonfly` and the **spines per
  group s** for `dragonfly-plus`.
- `--size paper` builds the size the paper evaluates: fat-tree k=64,
  leaf-spine k=64, 10×10 and 6×6×6 tori, and the Table 6 Dragonfly
  configurations. The OT network isn't in the paper, so it has no paper size.
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
  | dragonfly | a=4 (13 groups) | 104 | 52 |
  | dragonfly-plus | s=4 (9 groups) | 72 | 72 |
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
- **Dragonfly and Dragonfly+:** a global (inter-group) link on the path.
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

## Dragonfly and Dragonfly+

These come from the paper's Table 6, where they only appear in the task-grouping
analysis. The ns-3 evaluation doesn't use them, so there are no repo topology
files to check them against.

- **`dragonfly`:** g = a·h + 1 groups of `a` switches. Switches inside a group
  are wired all-to-all, each switch has `h` global links and `p` hosts, and
  every pair of groups shares exactly one global link. `--size a` uses
  h = a − 1 and p = a / 2. The default a=4 gives 13 groups, 52 switches and
  104 hosts; `--size paper` is a=16, h=8, p=8 (129 groups, 16,512 hosts).
- **`dragonfly-plus`:** g = s·h + 1 groups, each a small leaf-spine (s leaves
  and s spines, fully connected). Hosts hang off the leaves and the spines
  carry the global links. `--size s` uses h = s / 2 and 2 hosts per leaf. The
  default s=4 gives 9 groups, 72 switches and 72 hosts; `--size paper` is 33
  groups of 32 leaves + 32 spines with 64 hosts per leaf (67,584 hosts).

Global links are wired in the usual "consecutive" arrangement, and switch
names give the group: `df-g3-s1`, `leaf-g3-0`, `spine-g3-2`. The `traverse`
line describes routes as local hops inside a group plus the global links
between groups.

**What to expect under the paper's shortest-path routing:**

- **Dragonfly:** just over half of the furthest hosts are reached over a
  single path. The rest have 2–7 equally short paths, because going through an
  intermediate group (two global links) is as short as
  local → global → local.
- **Dragonfly+:** every furthest host has exactly one path, because only one
  spine in the source group owns the global link to each destination group.
- **No fabric link is a single point of failure in either.**
- **No OP cache size in `info`.** The paper has no closed-form formula for
  these, so `info` points to the Rust solver instead
  (`bash scripts/reproduce_six_tor_scope.sh dragonfly` in `solver/unit-merge`),
  which reproduces Table 6's 4,096 entries per switch.

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

The full `demo` repeats every case **50 times**. Each run draws new random
choices: which of the equally far/close hosts is the target, where the failure
goes, packet spraying, and flap drops. The runs use seeds `--seed`,
`--seed`+1, and so on, so the whole batch is reproducible.

Only the end results are printed:

- **One table per architecture:** for the furthest and the closest target,
  each failure case, and each method, the fraction of runs in which the
  method localized the failure. The healthy row shows false positives
  instead.
- **An overall table** across architectures.

The runs can be changed two ways:

- **`--runs N`** averages over N runs instead of 50.
- **Narrowing options** (`--target`, `--arch`, `--src`, or `--runs 1`) give
  one individual run with every case printed.

The results are averaged and written to **`opp_ping_demo.csv`** in the
current directory. **Every write replaces the previous file**; nothing is
appended and no per-seed copies are kept. `--csv PATH` picks another file
and `--no-csv` skips it. An individual run writes a CSV only when you give
`--csv PATH`, so it never replaces the averaged file by accident.

There is one row per **topology × target (furthest/closest) × injected failure
× method**. The default run has 7 × 2 × 7 cases, which gives 350 rows. The
"native multicast only" loop case has an OPP row only, because the baselines
don't depend on OPP's multicast mode. The CSV has no seed column.

| Group | Columns |
|---|---|
| Topology | `architecture`, `size`, `hosts`, `switches`, `links` |
| Flow | `source_ip`, `source_device`, `target` (furthest/closest), `switch_hops`, `equal_cost_paths` |
| Injected issue | `failure` (none, acl-drop, loop, link-flap, congestion, port-down), `failure_category`, `variant` (default, or usm/native for loops), `usm_rate_pct`, `blast_radius_hosts` |
| Method | `method`: OPP, R-Pingmesh (Ping + Traceroute), Ping (plain), RD-Probe (Ping + mirroring) |
| Averaging | `runs` (how many runs were averaged), `distinct_targets`, `distinct_injected_links` |
| Outcome | `probes_sent`, `detected`, `localized`, `false_positive`, `detection_rate`, `localization_rate`, `first_detection_at`, `first_localization_at`, `missed_probes`, `wrong_reports` |
| Cost and quality | `path_coverage_pct`, `full_coverage_rate`, `avg_rtt_us`, `max_rtt_us`, `switch_packets_per_probe`, `recirculations_per_probe` |

How the averages work:

- **Every number is the mean over the runs.**
- **The 0/1 columns become fractions of runs.** For example, `localized` =
  0.86 means the method localized the failure in 86% of the 50 runs.
- **`first_detection_at` and `first_localization_at`** are averaged only over
  the runs where that happened.
- **The target IP and injected link change from run to run,** so the CSV
  records how many different ones were used (`distinct_targets`,
  `distinct_injected_links`) instead of a single value. Run 1's terminal
  output shows actual examples.
- **For R-Pingmesh, `probes_sent`** includes the Traceroute probes.
- **Ping never localizes,** so its `localized` is always 0.
- **Blank cells** mean "not applicable".

The CSV holds the same averages as the printed tables, plus every other
metric.

For example, with pandas:

```python
import pandas as pd
df = pd.read_csv("opp_ping_demo.csv")
faults = df[(df.failure != "none") & (df.variant != "native")]
faults.pivot_table(index=["architecture", "target"], columns="method",
                   values="localized", aggfunc="mean")
```

## Tests

```bash
cd tools/opp-ping
python3 -m unittest discover -s tests -v          # 98 tests, about 3 s
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
- **The demo CSV:** 350 averaged rows with the expected columns and no seed
  column, every topology × target × failure × method combination present, at
  least 50 hosts and 50 switches on every row, and the default file
  overwritten rather than appended.
- **Demo modes:** the full demo prints only averaged tables, and narrowing
  options give a single run with case-by-case output and no CSV unless
  `--csv` is given.

`tests/test_dragonfly.py` checks:

- **Wiring:** one global link per pair of groups, and the per-switch local,
  global and host link counts.
- **Paper presets:** the Table 6 sizes.
- **Paths:** the path-count distribution and the group-aware `traverse` text.
- **Failures:** default failures land on a global link, single-probe
  localization works, and loops behave as on the other architectures.
- **`info`:** it points to the solver.

`tests/test_report.py` checks the averaging rules and the overwrite
behaviour.

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
