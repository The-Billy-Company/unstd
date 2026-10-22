# Evaluations

[performance](performance/README.md) enforces frozen per-public-function latency
ceilings with audited workloads. Its inventory lane is **active and opt-in**;
absolute timings require the baseline's recorded worker environment.

Current measurements and baselines live in the repository's
[benchmark harness](../../../../bench/README.md). Tests that verify the harness
itself belong under [gates/bench](../gates/bench/README.md), including the existing
opt-in timer calibration checks. Scaling and sustained measurements remain
separate obligations in the [coverage plan](../../../COVERAGE.md).
