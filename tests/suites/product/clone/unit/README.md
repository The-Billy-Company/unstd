# Cloning API and ownership contracts

`test_clone.py` compares structural cloning and dataclass conversion with stdlib copying, including mutation isolation, custom hooks, cycles, temporal values, and supported object types. `test_class_lifetime.py` uses weak references to detect class retention by acceleration metadata.

`test_admission.py` checks which classes the walker rebuilds itself: default-protocol dataclasses, enums, pydantic models and structs skip `deepcopy`, while state, reducer, `copyreg`, allocator, slots and copy-hook shapes are delegated one node at a time with their hooks honored. It also checks single admission per class and same-named class churn after collection. `test_hook_failures.py` places failing `__deepcopy__`, `__reduce_ex__` and `copyreg` hooks at every structural position, on both the Python and native entry paths, and requires the stdlib's exception object, an unchanged source, released partial twins and a working retry. Pydantic field/extra/private and msgspec `__post_init__` failures are included.

`test_depth_limits.py` runs deadline-owned workers at a fixed recursion limit. A depth sweep over list, dict, tuple, frozenset, dataclass and mixed chains requires success wherever `deepcopy` succeeds, with exact and independent chains; cyclic lists, dicts, dataclasses, sets and pydantic models must keep `deepcopy`'s shape, including aliases preserved by the whole-graph fallback.

`test_native.py` checks compiled traversal ownership, refusal, recursion/breadth budgets, copy-hook effects, and GC mutation safety. Native cases skip when the extension is not built. Pydantic and msgspec cases use the shared backend policy: they skip locally and fail under `--require-extras`.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/clone/unit -q
```

See [the module test overview](../README.md) for related evidence.
