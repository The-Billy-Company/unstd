# `unstd.clone`

Part of [`unstd`](../README.md). A fast **structural deep clone** for
wire-shaped data, behind a stdlib-`copy`-faithful surface.

`copy.deepcopy` is a pure-Python object-graph walker (it keeps a `memo` table to
preserve identity and handle cycles) and a well-known hot-path sink. For the
finite trees of plain data that cross a program's seams — `dict`/`list`/`tuple`/
`set`/`frozenset`/`dataclass`/`msgspec.Struct` — the fastest faithful clone is a
**structural round-trip** through [`msgspec`](https://github.com/jcrist/msgspec):
encode to bytes, then decode back to `type(obj)`.

```python
from unstd import clone

twin = clone.deep(payload)  # structural fast clone (deepcopy fallback)
shallow = clone.copy(payload)  # re-exported stdlib copy.copy
exact = clone.deepcopy(payload)  # re-exported stdlib copy.deepcopy
```

## Why it's fast — and why it stays faithful

`deep` gives two guarantees, not one:

- **Independence is structural.** A value rebuilt from bytes cannot share a
  single reference with the original, so the clone is deep and fully independent
  by construction. (This is _why the round-trip is encode→decode, not_
  `msgspec.convert(obj, type(obj))` — `convert` reuses already-correct nested
  objects, so its "clone" shares inner containers: a shallow copy in disguise,
  caught by this module's adversarial tests.)
- **Value fidelity is verified.** Decoding to the concrete type restores the
  outer type (a `Struct` stays a `Struct`, a `tuple` stays a `tuple`, a `set`
  stays a `set`), and for a fully-typed schema the whole value. An `Any`-typed
  slot can't carry a non-JSON-native type through JSON (a `tuple` in an `Any`
  field would come back a `list`; a non-`str` dict key stringified), so `deep`
  **compares the round-tripped clone to the original** and, on any mismatch — or
  any encode/decode error (non-finite float, int past 64-bit, cyclic graph,
  unencodable value) — falls back to `copy.deepcopy`. `deep` therefore only ever
  _returns_ a value-equal, independent clone.

Regenerate with `python3 -m bench --group clone --markdown` — the cases live in
`bench/cases.py` and `python3 -m bench verify` fails if a speedup falls through
its floor. Absolute times are whatever laptop ran them (here an M-series mac,
`msgspec==0.21.1`); the ratio is the part that travels.

| Case | `deep` | `copy.deepcopy` | Speedup |
| --- | --- | --- | --- |
| nested `dict` (200 records) | 102.2 µs | 605.1 µs | **5.9×** |
| pure-JSON `dict` (500 records) | 254.8 µs | 1.51 ms | **5.9×** |
| small `dict` | 925 ns | 1.1 µs | 1.2× |

The small-dict row is the floor case and the honest one: at three keys the
round-trip barely pays, because the encode/decode and the equality re-check that
makes `deep` safe cost about what walking three keys costs `deepcopy`.

## Surface

| Name        | Role                                                                                                                                                                                     |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `deep(obj)` | Fast structural clone: a verified `msgspec.json` encode→decode round-trip when `obj` is structural data and the extra is installed, else `copy.deepcopy`. Independent + type-preserving. |
| `copy`      | Faithful re-export of `copy.copy` (shallow).                                                                                                                                             |
| `deepcopy`  | Faithful re-export of `copy.deepcopy`.                                                                                                                                                   |

## `deep` is for data, not arbitrary objects

`deep` fast-paths only plain structural data. Anything else — objects with a
custom `__deepcopy__`, open file handles, locks, cyclic graphs, exotic
container subclasses — falls through to `copy.deepcopy` (and if the round-trip
can't faithfully reproduce a structural value, the eq-guard catches it and falls
back too, so `deep` never returns a _wrong_ clone). Use `deep` on wire structs and
plain data; reach for `deepcopy` directly for live objects.

## Backend

The fast path rides the **`clone` extra** (`pip install 'unstd[clone]'`), which
provides `msgspec` (pinned `msgspec==0.21.1` — the same pin the `serde` extra
carries, so the single root `uv.lock` resolves one version). Unlike
`serde.structs`, `clone` has a **faithful stdlib fallback**: without the extra —
a base `unstd` install — every call takes the `copy.deepcopy` path, same result
and only the structural speedup is missing. Consumers that opt into
`unstd[clone]` get the fast path automatically.

## Prior art

- **`msgspec`** (Jim Crist-Harif) — high-performance serialization + validation;
  the `msgspec.json` encode/decode pair performs the structural round-trip `deep`
  rides. (`msgspec.convert` is deliberately _not_ used — it shares nested objects
  rather than copying them.)
- stdlib **`copy`** — the general-purpose object-graph copier this accelerates
  for the structural-data case and falls back to for everything else.
