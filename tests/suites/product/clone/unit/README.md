# Cloning API and ownership contracts

`test_clone.py` compares structural cloning and dataclass conversion with stdlib copying, including mutation isolation, custom hooks, cycles, temporal values, and supported object types. `test_class_lifetime.py` uses weak references to detect class retention by acceleration metadata and requires native cache references to be released during direct module deallocation as well as cyclic collection.

`test_admission.py` checks which classes the walker rebuilds itself: default-protocol dataclasses, enums, pydantic models and structs skip `deepcopy`, while state, reducer, `copyreg`, allocator, slots and copy-hook shapes are delegated one node at a time with their hooks honored. It also checks single admission per class and same-named class churn after collection. `test_hook_failures.py` places failing `__deepcopy__`, `__reduce_ex__` and `copyreg` hooks at every structural position, on both the Python and native entry paths, and requires the stdlib's exception object, an unchanged source, released partial twins and a working retry. Pydantic field/extra/private and msgspec `__post_init__` failures are included.

`test_depth_limits.py` runs deadline-owned workers at a fixed recursion limit. A depth sweep over list, dict, tuple, frozenset, dataclass and mixed chains requires success wherever `deepcopy` succeeds, with exact and independent chains; cyclic lists, dicts, dataclasses, sets and pydantic models must keep `deepcopy`'s shape, including aliases preserved through the per-call memo and whole-graph fallback.

`test_native.py` checks compiled traversal ownership, refusal, recursion/container budgets, copy-hook effects, and GC mutation safety. Graph copies with a private memo and with a supplied Python memo must agree on exact types, source isolation, distinct equal nodes and aliases repeated through wide or densely shared graphs. Private-memo calls must release their source references on success, refusal and cyclic failure. Large scalar lists stay native, while excessively broad container graphs defer. A tuple-to-list cycle requires whole-graph alias preservation, and an isolated GC observer checks that partially initialized sequence storage is never visible to Python. Native cases skip when the extension is not built. Pydantic and msgspec cases use the shared backend policy: they skip locally and fail under `--require-extras`.

`test_shared_identity.py` checks repeated builtin containers, dataclasses and optional models/structs, references crossing custom-copy boundaries in both visitation orders, dict-key and set aliases, mixed cycles, one hook call per shared object and independent clone calls. It also pins `asdict`'s separate copies.

`test_temporal_graph.py` compares the canonical native temporal shortcut with the unchanged Python policy. It covers datetime/time identity, explicit source/timezone/timedelta memo entries, source pin order, naive `None` behavior, class/registry/function replacements, custom walkers and timezones, and the legacy extension import. Foreign memo-key equality and admitted-record state-key equality must preserve lookup counts and policy when callbacks mutate the memo or temporal bindings.

`test_scalar_prefix.py` checks source shrink, clear and growth at sequence allocation and at a later native child, initialized-only collector visibility, empty-sequence GC state and aliases after scalar prefixes. `test_repeat_cache.py` checks adjacent runs, caller memo precedence, foreign-hook overrides, separate `asdict` positions and mutation/collection of a completed child. Allocation-boundary cases require the forced-GC audit wrapper; ordinary native builds retain the positive ownership and alias checks.

`test_dataclass_recipes.py` checks conservative native record admission and state-copy ordering. A builtin-state positive control must stay in the native helper; supplied twins avoid allocation, unsafe or replaced allocators refuse before copying, and a dictionary-subclass state hook reuses the safely published parent and shared outer field memo. Source keep-alives, class replacement inside a direct callback and cyclic partial-twin collection cover native ownership. Custom allocation, attribute access, update lookup and live container mutation retain their observations on both backends.

`test_native_admission_boundaries.py` covers 60 combinations of Python, native and native-helper fallback dispatch. It fixes the source type before memo-key equality can change it, retains a selected dataclass handler after admission writes the memo, and requires custom keep-alive appends to run once in preorder. Atomic siblings ignore hook-written memo overrides. Ordinary and custom-new outer records check state continuations when the general native walker is disabled; an isolated low-limit worker also checks recursion reservations and recovery. A graph with 40 distinct dataclass types exercises recipe-cache overflow on both backends, retaining shared aliases and mutable-field isolation when memo equality changes the 33rd record's class.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/clone/unit -q
```

See [the module test overview](../README.md) for related evidence.
