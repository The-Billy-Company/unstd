# `unstd.clone`

Part of [`unstd`](../README.md). A fast **structural deep clone** for data, and
`dataclasses.asdict` on the same walker, behind a stdlib-`copy`-faithful surface.
Pure stdlib; no extra needed.

```python
from unstd import clone

twin = clone.deep(payload)  # structural fast clone
plain = clone.asdict(record)  # dataclasses.asdict, walked
shallow = clone.copy(payload)  # re-exported stdlib copy.copy
exact = clone.deepcopy(payload)  # re-exported stdlib copy.deepcopy
```

## Why it's fast

`copy.deepcopy` pays for generality on every node: a memo lookup, a dispatch
probe, and for anything but a builtin container a `__reduce_ex__` round-trip.
`deep` walks data with exact-type dispatch instead.

- **Leaves never pay a call.** Each container comprehension tests its children's
  exact type inline and returns atoms (`str`, `int`, `float`, `bool`, `None`,
  `bytes`, the immutable `datetime` values, …) as the same objects `deepcopy`
  returns for them.
- **Data classes are rebuilt the way `deepcopy` rebuilds them**, with the walker
  in place of the recursion. A plain dataclass is `cls.__new__` plus its walked
  `__dict__`. A pydantic model mirrors `BaseModel.__deepcopy__` line for line
  (fields, extras, private attributes, fields-set). A `msgspec.Struct` is re-built
  through its own reducer, so `__post_init__` runs exactly as under `deepcopy`.
  Each shape is admitted once per class, and only when the class leaves the
  default copy protocol alone.
- **Everything else goes to `copy.deepcopy`, one node at a time.** A custom
  `__deepcopy__`, an `OrderedDict` or `defaultdict`, an lxml element, a lock: that
  node is copied by `deepcopy` and its siblings keep walking.

Regenerate with `python3 -m bench --group clone --markdown`; `python3 -m bench
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
| `deep(obj)` | Structural clone: the exact-type walk for data, `copy.deepcopy` per foreign node, `copy.deepcopy` whole for a cycle. Value-equal, type-exact, fully independent. |
| `asdict(obj, *, dict_factory=dict)` | `dataclasses.asdict`'s result, walked. A custom factory, a non-dataclass argument, or a namedtuple / container subclass anywhere in the tree hands the call to the stdlib. |
| `copy` | Faithful re-export of `copy.copy` (shallow). |
| `deepcopy` | Faithful re-export of `copy.deepcopy`. |

## Where `deep` differs from `deepcopy`

Two places, both deliberate:

- **Shared references are not preserved.** An object reached twice is cloned
  twice, because keeping `deepcopy`'s memo is most of what makes it slow.
- **A cycle among walked containers** trips the recursion limit and the whole
  value is re-cloned by `deepcopy`, which handles it. Correct, not fast.

For live object graphs where aliasing is meaning, call `deepcopy` directly.

## Prior art

- stdlib **`copy`** — the general object-graph copier. `deep` reproduces its
  reconstruction for the shapes it walks and delegates everything else to it.
- **pydantic** `BaseModel.__deepcopy__` — mirrored field for field, with the walker
  standing in for its recursive `deepcopy`.
- stdlib **`dataclasses.asdict`** — the conversion `clone.asdict` reproduces, and
  the fallback for every container shape it special-cases.
