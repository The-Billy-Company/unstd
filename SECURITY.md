# Static security analysis and duplication

[Static analysis](.github/workflows/static-analysis.yml) is a required dependency
of CI and the reusable release gate. It also runs weekly and on manual dispatch.
A finding anywhere in the scanned source blocks the gate; there is no historical
baseline, changed-lines-only filter, or accepted duplicate budget.

| Check | Scope and failure policy |
| --- | --- |
| Bandit | All Python under `src/` and `hatch_build.py`, every severity and confidence. Findings, parse/read errors, absent reports and empty scans fail. |
| CodeQL `security-extended` | Python, C/C++, Rust and GitHub Actions. C analysis traces all default extensions and the opt-in Rust CPython adapter on Python 3.12, 3.13, 3.14 and 3.15. Every SARIF finding fails, including suppressed and unchanged alerts; malformed reports and failed invocations fail. |
| Rust Clippy | Every target in `native/text/rust`, using the locked offline crate. All warnings fail. |
| Pylint `duplicate-code` | All production Python modules. Repeated implementations of at least eight comparable lines fail. Comments, docstrings, imports and signatures do not count as executable duplication. |
| Ruff and mypy | Existing style, security-pattern and strict type checks continue in the quality job. |

Analyzer versions and their dependencies are committed in `uv.lock`; Actions
are pinned to commit SHAs. CodeQL's action manages its matching CLI and query
packs, so its weekly run can discover new issues without source changes.
JSON/SARIF output, duplication output, Rust diagnostics and gate-test results
are retained as CI artifacts for 30 days. GitHub's existing default CodeQL
setup continues publishing code-scanning diagnostics. The required extended
analysis saves SARIF artifacts with `upload: never`: GitHub rejects advanced
uploads while default setup is enabled. Full-source analysis and its strict
finding gate still run. If migrating the repository fully to advanced setup,
disable default setup and change this input to `always` together, following
[GitHub's migration guidance](https://docs.github.com/en/code-security/reference/code-scanning/troubleshoot-analysis-errors/results-different-than-expected). Repository settings must enable Code Security if this is
a private repository; the workflow must succeed before it can count as passing.

`main` requires the `CI / Required groups` aggregate from GitHub Actions,
requires the branch to be up to date, and enforces these checks for admins.
The aggregate waits for every SAST matrix job as well as the existing test and
supply-chain gates. Reusing this workflow alone does not configure protection
for other repositories.

## Local checks

```console
uv sync --locked --group dev --group static-analysis
mkdir -p .local/sast
uv run --no-sync bandit --recursive src hatch_build.py --severity-level all --confidence-level all --format json --output .local/sast/bandit.json
uv run --no-sync python -m tests.tooling.static_analysis bandit .local/sast/bandit.json
uv run --no-sync pylint src/unstd
cargo clippy --manifest-path native/text/rust/Cargo.toml --locked --offline --all-targets -- -D warnings
uv run --no-sync pytest tests/suites/assurance/gates/static_analysis -q
```

CodeQL runs in GitHub Actions. Its saved reports can also be checked locally
with `uv run --no-sync python -m tests.tooling.static_analysis sarif <report-or-directory>`.
The report validator supplements the scanner's exit status; it never replaces
running the scanner. CodeQL must finish successfully as well as produce clean
reports.

## Handling findings and shared logic

Reproduce a finding against the relevant public contract, fix production code,
and retain a regression that fails before the fix. Never add a baseline or
weaken the threshold to accept an existing defect. Reviewed false positives use
specific inline rule IDs with a reason. The current Bandit exceptions cover
explicit shell-free compiler/process calls and intentionally non-cryptographic
random generators; cryptographic randomness remains in `rand.crypto`.

DRY means each shared rule has one implementation. Strict Base64 validation and
regex absent-group projection share helpers rather than repeat branches. Keep
independent reference oracles, specialized algorithms, and backend-specific
contracts independent. Review repeated policy even below the duplication
threshold, including C/Rust code that Pylint does not analyze. Similarity checks
cannot prove the absence of all semantic duplication or security defects.

Tests deliberately seed insecure code and duplicated bodies to show the real
analyzers fail. Adversarial report tests and shell exit-status tests show that
findings and broken analysis cannot silently pass the CI/release gate.
