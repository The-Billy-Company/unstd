# Process runner API contracts

`test_proc.py` runs portable Python child commands to check literal argv without shell evaluation, timeout handling, captured stdout/stderr, checked failures, text/byte decoding, and the `capture`/`run_ok` conveniences.

`test_backend.py` checks native bounded waits after early pipe closure, timeout
and cancellation descriptor cleanup, non-inheritable notification handles,
remaining-deadline fallback, parent descriptor isolation and concurrent children.

No product extras are required. Real lifecycle/ownership experiments are documented in [integration](../integration/README.md) and [regression](../regression/README.md).

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/proc/unit -q
```

See [the module test overview](../README.md) for related evidence.
