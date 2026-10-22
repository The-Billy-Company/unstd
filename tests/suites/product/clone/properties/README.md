# Structural cloning properties

`test_clone_invariants.py` generates nested values and compares them with `copy.deepcopy` and `dataclasses.asdict`. It checks type/value fidelity, mutation isolation, the owned plain-tree path, and preservation of custom deepcopy protocols.

`test_mixed_trees.py` generates mixed trees of dicts, lists, tuples, sets, frozensets, dataclasses (including inherited and frozen), namedtuples, enums, `Decimal`, `bytes`, `bytearray` and immutable temporal values. Every position must have `deepcopy`'s exact type and value; no mutable node may be shared, and mutating either side must not reach the other. It pins the documented divergence that a shared alias is cloned separately, checks `asdict` against `dataclasses.asdict` (including identical errors), and requires the native and Python paths to agree on the same inputs, with a positive control that the native walker accepted the builtin trees.

`test_model_shapes.py` generates pydantic models with extras, private attributes and partial fields-sets, frozen and `unstd.model` models, and msgspec structs (keyword-only, array-like frozen, tagged, untracked). `model_copy(deep=True)` and `copy.deepcopy` are the oracles; isolation covers every reachable list and dict.

The core laws require no optional product backend. Pydantic and msgspec shapes skip locally when absent and fail under `--require-extras`; the native agreement law skips when the extension is not built. Hypothesis depth and example counts follow the shared test profiles.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/clone/properties -q
```

See [the module test overview](../README.md) for related evidence.
