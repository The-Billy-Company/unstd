# Time regressions

Status: **active**. `test_epoch_ms_precision.py` pins `from_epoch_ms` to whole
milliseconds at the calendar limits and far-future instants. The old float
division (`ms / 1000`) returned `9999-12-31T23:59:59.998993Z` for
`253402300799999`. Expected instants are hand-written calendar facts, and the
out-of-range `ValueError` is preserved.

Pure stdlib; no extra is required.

Run `uv run --no-sync pytest tests/suites/product/time/regression -q`.

See [the module test overview](../README.md) for related evidence.
