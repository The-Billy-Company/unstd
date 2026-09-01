# unstd properties

These tests exercise `unstd` with bounded, contract-valid generated values.
Expectations come from independent authorities rather than a second call to the
implementation:

- Python's `struct` defines binary bytes, offsets, sizes, and truncation errors.
- Python's `tomllib` defines TOML read semantics and validates optional-writer output.
- Python's `copy.deepcopy` defines clone fidelity; post-mutation source snapshots
  prove reference isolation.

Run this folder with `uv run --no-sync python3 -m pytest -q tests/properties`
from the package root.
