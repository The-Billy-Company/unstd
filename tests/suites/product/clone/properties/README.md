# Structural cloning properties

`test_clone_invariants.py` generates nested values and compares them with `copy.deepcopy` and `dataclasses.asdict`. It checks type/value fidelity, mutation isolation, the owned plain-tree path, and preservation of custom deepcopy protocols.

These laws require no optional product backend. Hypothesis depth and example counts follow the shared test profiles.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/clone/properties -q
```

See [the module test overview](../README.md) for related evidence.
