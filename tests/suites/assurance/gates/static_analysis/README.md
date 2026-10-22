# Static analysis evidence gate

The reusable SAST workflow retains SARIF scanner output and checks it with:

```console
python -m tests.tooling.static_analysis sarif .local/sast/codeql
python -m tests.tooling.static_analysis bandit .local/sast/bandit.json
uv run --no-sync pytest tests/suites/assurance/gates/static_analysis -q
```

The gate requires at least one report and one run per report, SARIF 2.1.0,
a named tool driver, and an explicit empty results list in every run. Any
result fails, including suppressed, unchanged-baseline, informational, and
severity-free findings. Reported invocation failures and scanner error
notifications also fail. A valid CodeQL report may omit rules and invocations.
Bandit JSON must explicitly contain empty `results` and `errors` lists and a
positive integer `metrics._totals.loc`, so an empty or failed scan cannot pass.

Tests use independent minimal reports and hostile mutations to prove missing,
malformed, truncated, duplicate-member, failed, and non-clean evidence cannot
pass. Real CLI checks cover recursive discovery, multiple reports/runs, failure
exit codes, and an empty report directory. The active parent `gates` lane owns
this directory.

Workflow tests run the actual Bandit and duplication shell commands with a stub
scanner to prove findings and tool failures preserve their exit status, including
through `tee`. They also check required CI/release wiring, pinned actions,
bounded jobs, complete CodeQL language coverage and both C build variants.
Independent unsafe-deserialization, shell-execution and duplicated-function
seeds prove the installed analyzers detect real problems. Those two tests skip
when their analyzer is absent from another test environment; the static-analysis
workflow requires both imports before running the gate suite.

[`../test_typing.py`](../test_typing.py) runs the real mypy and Ruff policies against independently
seeded valid and invalid source. It checks missing annotations, stub annotations,
specific suppression codes, missing imports, accidental dynamic decorator
signatures, unsafe overrides, uninitialized values, incomplete matches,
deprecated calls, unused awaitables, iterable truthiness, strict bytes, and
strict `None` comparisons. These tests require the development tools and skip
individually in the isolated SAST environment, which installs only its analyzers.
The main CI test environments install `dev` and run every typing control.
