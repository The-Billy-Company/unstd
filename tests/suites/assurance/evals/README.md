# Evaluations

[performance](performance/README.md) reserves cross-module speed and scaling
evidence. Its inventory lane is **planned** and contains no executable tests;
the suite runner refuses to report it as a passing run.

Current measurements and baselines live in the repository's
[benchmark harness](../../../../bench/README.md). Tests that verify the harness
itself belong under [gates/bench](../gates/bench/README.md), including the existing
opt-in timer calibration checks. Implement the obligations in the
[coverage plan](../../../COVERAGE.md) before activating a new evaluation lane.
