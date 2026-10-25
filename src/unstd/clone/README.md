# `unstd.clone`

Part of [`unstd`](../README.md). A fast **structural deep clone** for data, and
`dataclasses.asdict` on the same walker, behind a stdlib-`copy`-faithful surface.
No extra or third-party runtime dependency is needed. CPython builds optionally
include a bounded C walker for exact builtin graphs and admitted plain
dataclasses. Other shapes and builds without a compiler use the Python walker;
qualifying object state can still use native copying.

```python
from unstd import clone

twin = clone.deep(payload)  # structural fast clone
plain = clone.asdict(record)  # dataclasses.asdict, walked
shallow = clone.copy(payload)  # re-exported stdlib copy.copy
exact = clone.deepcopy(payload)  # re-exported stdlib copy.deepcopy
```

## Why it's fast

`copy.deepcopy` pays for generality on every node: a dispatch probe and, for
anything but a builtin container, a `__reduce_ex__` round-trip. `deep` walks data
with exact-type dispatch and shares one identity memo across the entire call.

The bundled C path admits exact `dict` (exact string keys), `list`, and `tuple`
trees containing exact scalar builtins. It preserves dictionary order, atomic
identity, shared references and independence from the source. Other trees use
the Python walker, including copy hooks, subclasses and sets. Plain admitted
dataclasses also use native reconstruction, and cycles follow the handling
described below.
Native work is bounded by 128 container levels and 65,536 container visits;
scalar values do not consume that budget, so wide scalar arrays remain native.
Exceeding either bound selects Python rather than limiting the public API.
The graph copier detects builtin cycles during copying and discards its
staged memo before selecting whole-graph `deepcopy`. The tree copier used by
`asdict` retains its eligibility pass.
Lists and tuples are filled into presized storage, and dictionaries retain
owned item iterators with live mutation checks. Incomplete
sequence storage stays hidden from the cyclic collector until it is safe to
inspect. Dataclass/model state and `asdict` fields also use native copying when
their contents qualify.
Builtin graphs track identities in a native pointer table. A successful root
copy needs no Python memo export; state boundaries publish completed copies to
the enclosing Python memo so aliases crossing object and copy-hook boundaries
still agree. Python fallback also avoids allocating an unused keep-alive list
for every node.
See the [native measurements](../../../bench/reports/native/native-results.md) for comparisons
against the previous Python implementation.

- **Exact builtin atoms stay shared.** Container loops test their children's
  exact type inline and return atoms (`str`, `int`, `float`, `bool`, `None`,
  `bytes`, …) directly. Immutable dates and durations use the identity memo;
  safe immutable temporal values may reuse the source or an existing memoized
  copy. Datetimes and times reconcile their timezone with that memo, including
  exact `datetime.timezone` and `zoneinfo.ZoneInfo`. Values with custom `tzinfo` use
  `deepcopy` with the same memo, preserving timezone copy hooks and isolating
  mutable timezone state.
- **Data classes are rebuilt the way `deepcopy` rebuilds them**, with the walker
  in place of the recursion. A plain dataclass is `cls.__new__` plus its walked
  `__dict__`. A pydantic model reconstructs its fields, extras, private state and
  fields-set with the same memo. A `msgspec.Struct` is re-built
  through its own reducer, so `__post_init__` runs as under `deepcopy`.
  Each shape is admitted once per class, and only when the class leaves the
  default copy protocol alone.
  Plain dataclasses also use native reconstruction for allocation, memo
  publication, state cloning and dictionary update. A bounded weak cache
  rechecks native layout admission when the class changes; custom allocators,
  attribute access and inherited layouts retain the Python recipe.
- **Everything else goes to `copy.deepcopy`, one node at a time.** A custom
  `__deepcopy__`, an `OrderedDict` or `defaultdict`, an lxml element, a lock: that
  node is copied by `deepcopy` with the same memo, so references crossing between
  custom objects and walked containers keep their identity.

The timings below predate shared-reference preservation. Regenerate with
`python3 -m bench --group clone --markdown`; `python3 -m bench
verify` fails if a speedup falls through its floor. Absolute times are whatever
laptop ran them (here an M-series mac, CPython 3.13); the ratio is the part that
travels.

| Case | `unstd` | stdlib | Speedup |
| --- | --- | --- | --- |
| clone.deep · nested (200 items) | 135.3 µs | 592.7 µs | **4.4×** |
| clone.deep · 500 records | 354.5 µs | 1.49 ms | **4.2×** |
| clone.deep · small dict | 261 ns | 1.1 µs | **4.1×** |
| clone.deep · 40 pydantic models | 82.9 µs | 185.2 µs | **2.2×** |
| clone.asdict · dataclass tree | 3.2 µs | 32.9 µs | **10.3×** |

The `asdict` gap is mostly `datetime` leaves: `dataclasses.asdict` hands every
non-atomic leaf to `deepcopy`, which rebuilds each one through its reducer.

## Why not a serialization round-trip

Up to 1.0.5, `deep` encoded to bytes with msgspec and decoded back, keeping the
result only if it compared equal to the source. On big JSON trees under CPython
3.13 that was faster still. It was also wrong: equality cannot see type. A nested
`StrEnum` came back a `str`, an `IntEnum` an `int`, an `OrderedDict` or
`defaultdict` a plain `dict` (dropping its factory), and every one of them passed
the guard. Making a round-trip type-faithful takes a verification walk that costs
about as much as the walker itself, so the round-trip is gone. Under CPython 3.14
the two measure the same on large trees anyway.

## Surface

| Name | Role |
| --- | --- |
| `deep(obj)` | Structural clone: the exact-type walk for data and `copy.deepcopy` per foreign node, sharing one identity memo. Owned plain data retains its types and values with independent mutable copies and preserved aliases; foreign hooks control their own result. |
| `asdict(obj, *, dict_factory=dict)` | `dataclasses.asdict`'s result, walked. A custom factory, a non-dataclass argument, or a namedtuple / container subclass anywhere in the tree hands the call to the stdlib. |
| `copy` | Faithful re-export of `copy.copy` (shallow). |
| `deepcopy` | Faithful re-export of `copy.deepcopy`. |

## Shared references and cycles

An object reached more than once maps to the same cloned object throughout one
`deep` call, including references crossing dataclass, model and custom-copy
boundaries. Custom copy hooks receive that same memo; aliases crossing into
those hooks are preserved when the hooks honor it. Separate calls create
independent mutable clones of owned data; immutable atoms may remain shared
with the source and across calls. This keeps graph relationships intact and
avoids repeating work for a shared subtree.

```python
shared = [1]
twin = clone.deep({"left": shared, "right": shared})
assert twin["left"] is twin["right"]
assert twin["left"] is not shared
```

The native graph copier detects builtin cycles while copying and selects
whole-graph `deepcopy`. Python reconstruction publishes mutable twins before
walking their children so backreferences reuse the same clone, with a
recursion-error fallback for remaining shapes. Immutable temporal values use
the memo to agree with foreign copy protocols. `deepcopy` remains the original
stdlib function for callers requiring its exact protocol.

`asdict` continues to follow `dataclasses.asdict`: repeated structural positions
are converted separately. Cycles among converted dataclasses, lists, dictionaries
and tuples raise the stdlib's recursion error; opaque leaves retain their own
`deepcopy` behavior, which can preserve cycles.

The [CPU profile](../../../bench/reports/clone/clone-cpu-results.md) records
measurements before shared-reference preservation, including collector-enabled
runs. Its alias-duplication contract describes that earlier implementation;
regenerate the profiles to compare the current behavior. The
[identity measurements](../../../bench/reports/clone/clone-shared-identity-results.md)
record the benefit on a shared graph and memo overhead on distinct trees.
The [optimized identity measurements](../../../bench/reports/clone/clone-shared-identity-optimized-results.md)
compare the native pointer table with the initial identity-preserving path.
The [dataclass measurements](../../../bench/reports/clone/clone-dataclass-identity-results.md)
compare native reconstruction with the previous Python recipe while preserving
field, record and copy-hook aliases.

The [cloning contract tests](../../../tests/suites/product/clone/unit/README.md)
cover shared fields, copy-hook boundaries, cyclic backreferences, source
isolation, class mutation, weak-cache lifetime and native/Python fallback.

## Prior art

- stdlib **`copy`** — the general object-graph copier. `deep` reproduces its
  reconstruction for the shapes it walks and delegates everything else to it.
- **pydantic** `BaseModel.__deepcopy__` — mirrored field for field, with the walker
  standing in for its recursive `deepcopy`.
- stdlib **`dataclasses.asdict`** — the conversion `clone.asdict` reproduces, and
  the fallback for every container shape it special-cases.
