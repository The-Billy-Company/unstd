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

## Backend

Both families ride the single **`containers` extra**
(`pip install 'unstd[containers]'` → `sortedcontainers` + `immutables`). Neither
has a faithful stdlib fallback, so — unlike `serde.jsonx`/`b64` — there is **no
slower-but-correct stdlib path**:

- **Importing the package is always safe.** The guarded imports bind a
  placeholder to each public name when a backend is absent, so `from
unstd.containers import ...` never crashes.
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
sl.bisect_left(2)  # O(log n) rank / range queries
sd = SortedDict({"b": 2, "a": 1})  # keys iterate in sorted order
ss = SortedSet([3, 1, 2, 1])  # set semantics + sorted, indexable
```

Backed by `sortedcontainers` (Grant Jenks). Its **list-of-lists** design keeps
each inner run short enough that `memmove`/`bisect` on native C lists beats a
tree's pointer-chasing in practice — competitive with (often faster than)
C-extension balanced trees while staying pure Python. `unstd.containers.SortedDict`
**is** `sortedcontainers.SortedDict` (a thin, faithful re-export — full upstream
API and docs apply).

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

That makes "snapshot this state, hand it to a concurrent reader" an `O(log n)`
no-copy, no-lock operation — the property stdlib has no container for.

Backed by `immutables`, a Hash Array Mapped Trie — Phil Bagwell's structure
("Ideal Hash Trees", 2001) — and the very implementation CPython vendored to back
[`contextvars`](https://peps.python.org/pep-0567/), so its persistence and
structural-sharing semantics are proven in the interpreter itself.
`unstd.containers.Map` **is** `immutables.Map` (thin, faithful re-export).
