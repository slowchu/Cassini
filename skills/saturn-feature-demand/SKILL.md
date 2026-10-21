---
name: saturn-feature-demand
description: Build an evidence-based Saturn recomp feature-demand report from retained hardware logs, captures and connected runtime source.
license: MIT
---

Use `hw_report` on explicitly selected hardware/guest logs. Record producer,
game/disc, route, run length, logging mode and whether rows are aggregate or
CPU-attributed. Do not add overlapping aggregate and per-CPU counts. Whole-run
totals are not a per-frame distribution.

For a correctness feature, connect the relevant accesses to existing runtime
behavior and a concrete original/native checkpoint. Use `inspect`/`diff` for
graphics evidence. Distinguish observed failure from a source-based risk; a
pattern absent from retained captures remains untested, not disproved.

For a speed proposal, access counts justify measuring a candidate path. They
do not establish host cost or removable work. Require an admitted host CPU
measurement before choosing an optimization, with scene/platform uncertainty
and existing idle/event behavior accounted for. Do not reinterpret guest work
ledgers or handoff wall time as exclusive CPU time.

Produce a short ranked report: observed need, source premise, evidence limits,
next discriminating check and explicit conditions for implementation. Keep
source/raw data private and do not install tools or launch a profiler merely
because a report recommends measuring later.
