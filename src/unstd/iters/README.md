# `iters` — lazy iterator & functional combinators

Part of [`unstd`](../README.md). The curated, `more-itertools`-style
set of combinators the stdlib leaves as copy-paste "recipes" — chunk a stream,
slide a window, take the first N, drop duplicates in order, split on a predicate
in one pass. It kills hand-rolled accumulator loops across the codebase.

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

Everything returns a **lazy iterator** unless the name implies materialization
(`take` → `list`). Nothing re-implements a primitive stdlib already provides
identically — only the missing combinators, plus two convenience re-exports
(`itertools.batched`, `itertools.pairwise`) so this is a single import home.

## Surface

| Function                                | Returns                  | What it does                                                                                                                          |
| --------------------------------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| `chunked(iterable, n, *, strict=False)` | `Iterator[tuple]`        | consecutive `n`-length tuples; superset of stdlib `itertools.batched` (3.13) with a validated `n` and `strict` uneven-tail guard      |
| `windowed(iterable, n, step=1)`         | `Iterator[tuple]`        | overlapping length-`n` sliding windows (complete windows only); size/stride superset of `itertools.pairwise`                          |
| `runs(iterable, key)`       | `Iterator[(key, tuple)]` | run-length grouping — merge _consecutive_ equal-keyed items (each run materialized so `groupby`'s transient sub-iterators can't bite) |
| `first(iterable, default=…)`            | item                     | first item (consumes one); `ValueError`/`default` on empty                                                                            |
| `last(iterable, default=…)`             | item                     | last item (`reversed` for sequences, `maxlen=1` deque drain otherwise)                                                                |
| `nth(iterable, n, default=…)`           | item                     | item at 0-based index `n` (consumes ≤ `n+1`)                                                                                          |
| `take(iterable, n)`                     | `list`                   | first `n` items — **materializes** a bounded prefix                                                                                   |
| `ilen(iterable)`                        | `int`                    | count without building a list (`deque(zip(it, count()), maxlen=0)` drain); exhausts the iterable                                      |
| `unique_everseen(iterable, key=None)`   | `Iterator`               | drop _any_ later repeat, first wins — hardened for **unhashable keys**                                                                |
| `unique_justseen(iterable, key=None)`   | `Iterator`               | collapse only _consecutive_ duplicates (O(1) memory)                                                                                  |
| `partition(pred, iterable)`             | `(falsy_it, truthy_it)`  | split into false/true streams in **one shared pass** (`tee`), never double-consuming the source                                       |
| `flatten(iterable_of_iterables)`        | `Iterator`               | flatten one level (`chain.from_iterable`, renamed)                                                                                    |
| `batched` · `pairwise`                  | (stdlib)                 | re-exported from `itertools` for one-stop importing                                                                                   |

```python
from unstd.iters import chunked, windowed, runs, partition, unique_everseen

list(chunked("abcdefg", 3))  # [('a','b','c'), ('d','e','f'), ('g',)]
list(windowed([1, 2, 3, 4], 2))  # [(1,2), (2,3), (3,4)]
[(k, g) for k, g in runs([1, 1, 2, 1], lambda x: x)]
# [(1,(1,1)), (2,(2,)), (1,(1,))]
evens, odds = partition(lambda n: n % 2, range(6))  # ([0,2,4], [1,3,5]) once drained
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
