---
doc_radar:
  sentinels:
    - file: native/clone/src/adapter.c
      contains: ['clone_model_state(', 'clone_temporal_sequence(', 'temporal_memberships_safe(', 'exact_type_membership(']
---

# Native structural cloning

[`src/adapter.c`](src/adapter.c) is the complete C implementation and CPython
binding for `unstd.clone._clone_native`; its filename is independent of the
installed module name. [`src/README.md`](src/README.md) maps its native entrypoints.

The [Python walker](../../src/unstd/clone/structural.py) admits copy protocols
and chooses graph-preserving `deep` or positional `asdict` semantics. Native
calls handle exact builtin containers, sets and admitted plain dataclasses, then
return `NotImplemented` when the walker must take over. Canonical public calls
continue proved builtin cycles in the shared-memo walk; legacy direct calls
retain their explicit cycle error.

Canonical model recipes continue inside the shared graph walk. They allocate
once and retain the Python recipe's state-read, state-write and callback order;
replaced handlers and state walkers keep their Python continuation.

Flat root temporal sequences use one allocation followed by policy and leaf
validation before filling. The allocation-free section stays within the normal
65,536-visit budget; unsupported shapes retain the general graph walk.

Policy certification follows CPython's probe order instead of scanning every
entry. The dictionary prefix and probe geometry are qualified for standard GIL
builds of CPython 3.12–3.14; visible custom probe overrides select the
conservative fallback. Custom membership keeps its callback order, live walker
changes and exact errors.

The [build hook](../../hatch_build.py) compiles this source for CPython GIL
builds. When changing admission or reconstruction, check both native paths and
[product tests](../../tests/suites/product/clone/README.md): GC/finalizers can
mutate a source during allocation, and a dataclass twin cannot be recreated
after observable state callbacks have begun.

Record layout caches retain weak class references and accept only valid,
nonzero CPython versions. Python 3.12 also requires `VALID_VERSION_TAG`, since a
failed assignment can leave a nonzero invalid tag. Python 3.13 and 3.14 use
nonzero tags; other interpreter versions retain uncached layout checks.
