# Release verifier gates

`test_verifier.py` constructs small wheel/sdist fixtures and corrupts them to
check the stdlib-only [release verifier](../../../../tooling/release/verify.py). It
requires safe archive members, consistent package/version/tag metadata, valid
wheel RECORD hashes and sizes, bounded decompression, an admitted distribution
set, and an exact publication manifest. Isolated CLI tests exercise manifest
creation and revalidation without installing the package.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/release -q
```

These tests participate in the default `gates` suite. Acceptance of actual built
artifacts is covered by the [packaging plane](../../planes/packaging/README.md);
the [supply-chain runbook](../supply_chain/README.md) describes publication wiring.

`test_ci_reuse.py` checks that releases reuse only recent repository-owned CI
for the exact commit, wait for pending runs, and reject failed or altered
evidence. Missing or stale evidence requests the complete reusable CI gate.
Artifact construction can run concurrently, but publication requires both a
successful CI decision and verified distributions.

`test_wheel_tests.py` checks that the installed-wheel runner executes every test
exactly once across complete partitions, starts bounded workers concurrently,
preserves the caller's working directory, and retains all worker output and
failures, including pytest's no-tests exit. The runner adds no dependencies and
keeps each test's existing Hypothesis profile. Cibuildwheel invokes it as
`python {project}/tests/tooling/release/wheel_tests.py --workers 4`; the worker
count is capped by the CPUs available to the interpreter.

`test_tls_runtime.py` verifies the stdlib-only
[`runtime/`](../../../../tooling/runtime/README.md) preflight against vulnerable
and patched releases in every reviewed OpenSSL branch, prereleases, unknown
engines, inconsistent version metadata and missing TLS 1.3. Its isolated CLI
test runs with site packages disabled. Run the preflight with each actual build,
test and deployment interpreter; a dependency's private OpenSSL is unrelated.

`test_audio_workflow.py` checks the required read-only Linux audio qualification
job, authenticated build/test dependencies, pinned source/fix downloads,
same-job builder trust policy, authenticated local wheel installation, and
actual-loaded-library preflight before audio contracts. Negative checks reject
download corruption and wheel/provenance tampering without network or builds.
