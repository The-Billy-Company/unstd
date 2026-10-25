---
doc_radar:
  sentinels:
    - file: src/unstd/iters/__init__.py
      contains: ['from unstd.iters.consecutive import chunked, runs, windowed', 'from unstd.iters.select import first, ilen, last, nth, take']
    - file: src/unstd/iters/reshape.py
      contains: ['def partition[T](']
---

# `iters` — lazy iterator & functional combinators

Part of [`unstd`](../README.md). The curated, `more-itertools`-style
set of combinators the stdlib leaves as copy-paste "recipes" — chunk a stream,
slide a window, take the first N, drop duplicates in order, split on a predicate
in one pass. It kills hand-rolled accumulator loops across the codebase.

## Source ownership

This flat leaf groups the combinators by their work on a stream. Its package
initializer exposes the shared public surface without adding package layers.

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Public combinator exports plus stdlib `batched` and `pairwise` |
| [`consecutive.py`](consecutive.py) | Chunks, sliding windows, and consecutive-key runs |
| [`distinct.py`](distinct.py) | Global and consecutive duplicate removal, including unhashable keys |
| [`reshape.py`](reshape.py) | One-level flattening and shared-pass predicate partitioning |
| [`select.py`](select.py) | First, last, and indexed selection, bounded prefixes, and counting |

Run the [iterator contracts](../../../tests/suites/product/iters/README.md) from
the repository root with `uv run --no-sync pytest tests/suites/product/iters -q`.

## Pure stdlib — no extra, no backend

Unlike the other `unstd` groups (`serde`/`ids`/`time`/`model`), `iters` carries
**no optional extra and no third-party backend.** It is purely _additive_
combinators implemented natively on top of `itertools`/`functools`, so it is
always available in a base `unstd` install at **zero dependency weight** —
exactly the "any Python service depends on `unstd` for free" invariant
exists to protect. There is nothing to `pip install 'unstd[iters]'`; just:

```python
from unstd.iters import chunked, windowed, first, unique_everseen, partition
```

Combinators return **lazy iterators**; selectors (`first`, `last`, `nth`) return
one item, `ilen` returns a count, and `take` materializes a list. Nothing re-implements a primitive stdlib already provides
identically — only the missing combinators, plus two convenience re-exports
(`itertools.batched`, `itertools.pairwise`) so this is a single import home.

## Surface

| Function                                | Returns                  | What it does                                                                                                                          |
| --------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `chunked(iterable, n, *, strict=False)` | `Iterator[tuple]`        | consecutive `n`-length tuples; validates `n` and supports a `strict` uneven-tail guard on Python 3.12+, where stdlib `batched` gained `strict` in 3.13 |
| `windowed(iterable, n, step=1)`         | `Iterator[tuple]`        | overlapping length-`n` sliding windows (complete windows only); size/stride superset of `itertools.pairwise`                          |
| `runs(iterable, key)`       | `Iterator[(key, tuple)]` | run-length grouping — merge _consecutive_ equal-keyed items (each run materialized so `groupby`'s transient sub-iterators can't bite) |
| `first(iterable, default=…)`            | item                     | first item (consumes one); `ValueError`/`default` on empty                                                                            |
| `last(iterable, default=…)`             | item                     | last item (`reversed` for sequences, `maxlen=1` deque drain otherwise)                                                                |
| `nth(iterable, n, default=…)`           | item                     | item at 0-based index `n` (consumes ≤ `n+1`)                                                                                          |
| `take(iterable, n)`                     | `list`                   | first `n` items — **materializes** a bounded prefix                                                                                   |
| `ilen(iterable)`                        | `int`                    | count without building a list (`deque(zip(it, count()), maxlen=0)` drain); exhausts the iterable                                      |
| `unique_everseen(iterable, key=None)`   | `Iterator`               | drop _any_ later repeat, first wins — hardened for **unhashable keys**                                                                |
| `unique_justseen(iterable, key=None)`   | `Iterator`               | collapse only _consecutive_ duplicates (O(1) memory)                                                                                  |
| `partition(iterable, pred)`             | `(falsy_it, truthy_it)`  | split into false/true streams in **one shared pass** (`tee`); each element drawn once, `pred` and its result's truth test called once                             |
| `flatten(iterable_of_iterables)`        | `Iterator`               | flatten one level (`chain.from_iterable`, renamed)                                                                                    |
| `batched` · `pairwise`                  | (stdlib)                 | re-exported from `itertools` for one-stop importing                                                                                   |

```python
from unstd.iters import chunked, windowed, runs, partition, unique_everseen

list(chunked("abcdefg", 3))  # [('a','b','c'), ('d','e','f'), ('g',)]
list(windowed([1, 2, 3, 4], 2))  # [(1,2), (2,3), (3,4)]
[(k, g) for k, g in runs([1, 1, 2, 1], lambda x: x)]
# [(1,(1,1)), (2,(2,)), (1,(1,))]
evens, odds = partition(range(6), lambda n: n % 2)  # ([0,2,4], [1,3,5]) once drained
list(unique_everseen([1, 2, 1, 3, 2]))  # [1, 2, 3]
```

## Prior art

These mirror the official Python
[`itertools` recipes](https://docs.python.org/3/library/itertools.html#itertools-recipes)
section and the same-named helpers in
[`more-itertools`](https://github.com/more-itertools/more-itertools) (Bettini et
al.). They are **native reimplementations, not a dependency** — adopting the
recipe shapes without pulling `more-itertools` into `unstd`'s (empty) runtime
closure, which is the whole point of the pure-stdlib base.

`unique_everseen` compares equal keys across its hashable and unhashable
histories: a set and an equal frozenset count as the same value, in either order.
Hashable keys retain the fast set lookup; mixed histories need cross-comparison.
`partition` freezes each predicate result to a boolean before sharing it, so
mutable results and side-effecting truth tests cannot change the other side's
membership.
