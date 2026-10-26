# Package description and startup gates

`test_package.py` compares package groups, extras, version, documentation, typing
marker, imports, and dependency metadata against the checkout and
`pyproject.toml`. It prevents the package's advertised surface from drifting away
from what ships. `test_startup.py` uses fresh interpreters and import blockers to
check lazy distribution metadata lookup, version caching/reload, and codec reuse.
`test_source_inventory.py` requires every registered native adapter in the frozen
build source, including Linux-only adapters when the builder runs on another OS.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/assurance/gates/packaging -q
```

These ordinary gates check descriptions and startup behavior. Real offline
wheel/sdist builds and pristine installed consumers belong in the separate
[packaging plane](../../planes/packaging/README.md).
