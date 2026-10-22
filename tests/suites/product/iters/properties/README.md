# Iterator reference laws

`test_reference_laws.py` compares every combinator with plain-loop reference
specifications written from the documented contracts. Generated inputs cover
finite streams with mixed hashability, sources that raise at item N, and
counted sources pulled k times. A failing source must yield exactly the
reference prefix and then propagate its own exception; each output must read
exactly the items it logically requires. Arbitrary partition pull schedules
keep stable splits, one predicate call per item in source order, and surface a
source failure on whichever side reaches it.

These laws require no optional product backend. Hypothesis example counts
follow the shared test profiles.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/iters/properties -q
```

See [the module test overview](../README.md) for related evidence.
