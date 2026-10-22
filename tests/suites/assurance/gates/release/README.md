# Release verifier gates

`test_verifier.py` constructs small wheel/sdist fixtures and corrupts them to
check the stdlib-only [release verifier](../../../../tooling/release.py). It
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
