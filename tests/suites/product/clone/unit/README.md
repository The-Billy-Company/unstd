# Cloning API and ownership contracts

`test_clone.py` compares structural cloning and dataclass conversion with stdlib copying, including mutation isolation, custom hooks, cycles, temporal values, and supported object types. `test_class_lifetime.py` uses weak references to detect class retention by acceleration metadata.

`test_native.py` checks compiled traversal ownership, refusal, recursion/breadth budgets, copy-hook effects, and GC mutation safety. Native, Pydantic, and msgspec cases skip when their capabilities are unavailable.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/clone/unit -q
```

See [the module test overview](../README.md) for related evidence.
