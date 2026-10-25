# `containers` — the collections stdlib is missing

Part of [`unstd`](../README.md). `collections` already ships the
containers that are hard to beat — `deque`, `Counter`, `defaultdict`,
`OrderedDict` are C-backed and left exactly where they are. This group adds only
the structures the stdlib has **no equivalent for**.

| Name                                                             | Wraps                                                               | Use for                                                                                           |
| ---------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| [`SortedDict` · `SortedList` · `SortedSet`](#sorted-collections) | [`sortedcontainers`](https://grantjenks.com/docs/sortedcontainers/) | collections that stay sorted through inserts/deletes — leaderboards, range scans, ordered indexes |
| [`Map`](#map--persistent-immutable-hamt)                         | [`immutables`](https://github.com/MagicStack/immutables)            | a persistent immutable mapping — lock-free snapshots, structural-sharing state versions           |

```python
from unstd.containers import SortedDict, SortedList, SortedSet, Map
```

## Source ownership

This package is a flat leaf: its public modules and native adapters share one
small collection API without additional package boundaries.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Lazy public type exports |
| [`sorted.py`](sorted.py) | Sorted backend selection and public pickle paths |
| [`ordering.py`](ordering.py) | Accelerated sorted subclasses and native bindings |
| [`persistent.py`](persistent.py) | Guarded persistent map export |
| [`_sorted_native.pyi`](_sorted_native.pyi) | Native ABI declarations; [C source ownership](../../../native/containers/README.md) |

Run the [container contract suite](../../../tests/suites/product/containers/README.md)
with `uv run --no-sync pytest tests/suites/product/containers -q`.

## Backend

Both families ride the single **`containers` extra**
(`pip install 'unstd[containers]'` → `sortedcontainers` + `immutables`). Neither
has a faithful stdlib fallback, so — unlike `serde.jsonx`/`b64` — there is **no
slower-but-correct stdlib path**:

- **Importing the package is always safe.** The guarded imports bind a
  placeholder to each public name when a backend is absent, so `from
unstd.containers import ...` never crashes.
- **Backends load on first access to their types.** `import unstd.containers`
  loads neither backend; importing `Map` loads only `immutables`, and importing
  a sorted type loads only its sorted backend. Resolved names are cached.
  `dir()` lists the public types without
  loading them; importing all four types loads both backends.
- **Constructing a backed type without its backend raises.** The placeholder
  raises a clear, actionable `ImportError` naming the extra (the deferred-raise
  variant of `serde.structs`'s posture). A consumer that never touches these
  types pays nothing to import the package.

`HAVE_SORTEDCONTAINERS` (`unstd.containers.sorted`) and `HAVE_IMMUTABLES`
(`unstd.containers.persistent`) expose which backends resolved, for callers that
want to branch instead of catching the `ImportError`.

## Sorted collections

Three collections that keep themselves sorted — the gap `bisect` over a
hand-maintained list only half-fills (an insert into a plain list is `O(n)` and
trivially desynced).

```python
sl = SortedList([3, 1, 2])
sl.add(0)  # -> [0, 1, 2, 3], always sorted
sl.bisect_left(2)  # rank of the first 2
sd = SortedDict({"b": 2, "a": 1})  # keys iterate in sorted order
ss = SortedSet([3, 1, 2, 1])  # set semantics + sorted, indexable
```

Backed by `sortedcontainers` (Grant Jenks). Its **list-of-lists** design uses
native list insertion and binary search within short runs. Performance depends
on workload, collection size and load factor; the first positional query can
also build an index. Range iteration costs time proportional to the number of
returned values. On supported CPython builds, bundled C kernels accelerate rank
and positional queries, plus eligible integer batches, in
compatible subclasses of the upstream sorted classes. The upstream APIs and
ordering semantics apply, including keyed collections, views, range scans,
copying and pickling. Accelerated classes have their own identities; they remain
subclasses of their upstream counterparts.

Without the native extension, on other interpreters or with an unqualified
backend version, the original upstream classes remain available. The
`containers` extra is required in either case; the extension accelerates the
backend's algorithms rather than providing a substitute for the dependency.

Ordered integer indexes can use the existing batch APIs for ingestion and
expiration:

```python
ids.update(next_ids)  # increasing list/tuple at or beyond the current maximum
del ids[:ids.bisect_left(cutoff)]  # expire the oldest IDs or timestamps
```

The native mutation paths target substantial batches below the backend's
rebuild thresholds. Prefix/suffix deletion drops whole blocks and delegates a
partial boundary to the backend. Increasing exact signed 64-bit integer
list/tuple updates stage replacement tail blocks. Generic values, keyed
collections, custom subclasses, helper overrides and aliased storage retain
upstream mutation behavior. Individual `add`, `remove`, `discard` and `pop`
also retain their upstream algorithms. Performance depends on batch size and
ownership of the removed values; the
[mutation evaluation](../../../bench/reports/containers/mutations/README.md)
records both retained and exclusively owned integer controls.

## `Map` — persistent immutable HAMT

A genuinely immutable mapping. `MappingProxyType` is a _view_ (mutate the backing
dict and the "frozen" view changes underneath you); `Map` is a _value_ — `set` /
`delete` return a **new** map and leave the original untouched, sharing all
unchanged structure instead of deep-copying.

```python
m1 = Map(a=1, b=2)
m2 = m1.set("c", 3)  # new map; m1 is unchanged
m1["a"], "c" in m1  # (1, False)
m2["c"]  # 3
```

Updates share unchanged trie branches rather than copying the whole mapping.
Stored values are shared references: a `Map` containing a list does not freeze
that list, so snapshot isolation also depends on how callers manage its values.

Backed by `immutables`, a Hash Array Mapped Trie — Phil Bagwell's structure
("Ideal Hash Trees", 2001) — and the very implementation CPython vendored to back
[`contextvars`](https://peps.python.org/pep-0567/), so its persistence and
structural-sharing semantics are proven in the interpreter itself.
`unstd.containers.Map` **is** `immutables.Map` (thin, faithful re-export).

When only the final version of a batch is needed, use the backend's bulk APIs
instead of creating an intermediate map after every edit:

```python
m2 = m1.update({"c": 3, "d": 4})
with m1.mutate() as edits:
    edits["c"] = 3
    del edits["a"]
    m3 = edits.finish()
```

Both preserve `m1`; retain repeated `set`/`delete` calls when callers need every
intermediate version. For sorted collections, use `update(iterable)` when the
entire batch is available so the backend can choose a merge/rebuild strategy.

The [performance evaluation](../../../bench/reports/containers/README.md) records
paired Linux and macOS startup and operation measurements. Lazy exports reduce
startup work for consumers of one family. Native kernels target sorted-list
queries and their use inside sorted dictionaries and sets. Eligible integer
batch mutations add native paths; `Map` retains its existing native
`immutables` implementation.
