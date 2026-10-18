The test suite now follows Billy's Python organization: module-owned contracts
under `tests/suites/product`, cross-cutting assurance under `tests/suites/assurance`,
shared support, and standalone tooling. A validated inventory distinguishes active
coverage from planned lanes and provides `python -m tests.tooling list|check|run`.
CI checks inventory drift; pytest uses strict markers and shared generation profiles.
Existing TLS policy tests now run without the BLAKE3 extra. The coverage plan maps
the remaining property, fuzz, penetration, fault, installation, concurrency,
resource, and performance work without counting scaffolding as implemented tests.
Existing wall-clock benchmark calibration checks retain their assertions under
the explicit `perf` marker, outside ordinary correctness runs.
