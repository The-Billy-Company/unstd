# Static analysis evidence gates

Run `python -m tests.tooling.static_analysis bandit <report.json>` or
`python -m tests.tooling.static_analysis sarif <report-or-directory>` after the
actual scanner succeeds. Findings, incomplete scans and malformed evidence fail;
reports never substitute for running a scanner. The required workflow and
[security policy](../../../SECURITY.md) define full-source coverage.
