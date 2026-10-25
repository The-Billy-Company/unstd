# Process runner API contracts

`test_proc.py` runs portable Python child commands to check literal argv without shell evaluation, timeout handling, captured stdout/stderr, checked failures, text/byte decoding, and the `capture`/`run_ok` conveniences.

`test_backend.py` checks native bounded waits after early pipe closure, timeout
and cancellation descriptor cleanup, non-inheritable notification handles,
remaining-deadline fallback, parent descriptor isolation and concurrent children.

`test_native_spawn.py` proves accepted native calls, PATH/cwd/environment behavior,
audit refusal and mutation, hidden private owners during GC callbacks, safe empty
search rejection, descriptor isolation and cancellation ownership.

`test_native_io.py` checks exact Unicode/newline and growth-boundary capture,
partial timeout bytes, flags/descriptor ownership, retries, stdin fallback and
SIGINT cleanup. A Linux address-space limit forces a real resize `MemoryError`
and verifies kill, reap and recovery; sanitizer runs use their separate address
space configuration.

No product extras are required. Real lifecycle/ownership experiments are documented in [integration](../integration/README.md) and [regression](../regression/README.md).

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/proc/unit -q
```

See [the module test overview](../README.md) for related evidence.
