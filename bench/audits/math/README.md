# Mathematical backend audit

Research and measurements for the October 22, 2026 backend review.
The [report](report.md) explains the decisions, security boundaries, and limits
of the evidence. `inventory.json` maps every discovered public callable and
locally declared method to its current backend and audit decision, including
non-mathematical APIs so the scope is explicit.

The [implementation record](implementation.md) covers the follow-up changes and
their separate measurements. The original report and inventories remain a
snapshot of the audit baseline.

`probe_text.py` measures complete text calls with paired, alternating wall and
thread CPU samples. Reproduce from the repository root with the existing extras:

```console
.venv/bin/python bench/audits/math/probe_text.py --output /tmp/math-text.json
```

`text-results.json`, `random-results.json`, and `numeric-results.json` are
observations from one shared macOS arm64 host. Noise is retained, and none is a
cross-platform performance guarantee. The random and numeric probe sources are
retained as `probe_random.py` and `probe_numeric.py`; both require an explicit
`--output PATH` for new measurements. Use `--validate-only` on the random probe
or `--verify-only` on the numeric probe to run correctness checks without timing.
The exact originally timed sources are preserved as `random-measured-source.txt`
and `numeric-measured-source.txt`. Their polished drivers changed callback
wrappers; see `random-provenance.json` and `numeric-provenance.json`. The raw
measurements belong to the original sources, not the polished drivers.

The original audit did not change production functions, dependency requirements,
build configuration, or benchmark ceilings. The implementation follow-up changes
selected production/build paths and adds qualification tooling; existing benchmark
ceilings are preserved. Earlier crypto and native text experiments retain their
original measurement dates and runtimes.
