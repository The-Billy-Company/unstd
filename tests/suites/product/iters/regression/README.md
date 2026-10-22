# Iterator regressions

`test_windowed_laziness.py` retains the eager-stagger defect: `windowed` with
the default step consumed `n - 1` source items inside the call, before any
window was requested. A counted source must read nothing until the first pull
and exactly `n` items for the first window, an abandoned iterator must leave a
shared source untouched, and a failing source must raise its own exception from
iteration. The three cases failed against the original implementation.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/iters/regression -q
```

See [the module test overview](../README.md) for related evidence.
