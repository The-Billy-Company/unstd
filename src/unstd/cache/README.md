# Persistent result cache

`Cache` turns expensive JSON-producing work into a result shared by local
threads, worker processes, and later application runs. It composes unstd's
existing [`serde.jsonx`](../serde/README.md) codecs with stdlib SQLite. The base
install is sufficient; optional serialization backends can accelerate admitted
JSON operations.

```python
from unstd.cache import Cache

cache = Cache(".local/results.sqlite3", namespace="search-v1", max_entries=10_000)


def build_result():
    return {"matches": [{"id": "example", "score": 0.95}]}


result = cache.get_or_compute("query:example", build_result, ttl=300)
# Another local worker or a later run reads the persisted result.

# Store or read a complete batch with one connection and transaction.
cache.set_many({"query:a": {"score": 1}, "query:b": {"score": 2}})
results = cache.get_many(["query:a", "query:b", "query:missing"])
```

Use versioned namespaces or keys when the producer's logic or inputs change.
Keys are explicit strings; include every input that determines a result. The
cache does not infer a function's dependencies.

## API

```text
Cache(path, *, namespace="default", timeout=30.0, max_entries=None)
cache.get(key, default=None)
cache.set(key, value, *, ttl=None)
cache.get_many(keys) -> dict[str, Json]
cache.set_many(values, *, ttl=None)
cache.delete(key) -> bool
cache.clear() -> int
cache.get_or_compute(key, factory, *, ttl=None, codec=None)
```

- `path` names a dedicated local disk database. Missing parent directories are
  created with `fs.ensure_dir`. New database files are created with mode 0600;
  existing file modes are preserved. SQLite also manages temporary WAL sidecars.
- `namespace` isolates keys, expiration cleanup, FIFO retention, and `clear()`
  from other namespaces in the same database.
- `timeout` is a finite nonnegative number of seconds for each lock-acquisition
  phase. All required file locks share one deadline; SQLite lock waits have
  separate budgets. It is not a producer or total-operation deadline. A timeout
  raises `sqlite3.OperationalError`; zero tries once without waiting.
- `max_entries=None` keeps an unbounded number of live entries. A positive
  integer bounds entries within this namespace when this instance writes.
  Writes remove expired entries, then evict the oldest written entries first.
  Overwriting a key refreshes its position; reads leave the order unchanged.
- `ttl=None` persists a value without expiration. A finite nonnegative TTL starts
  after encoding a successful write or producer result, immediately before
  storage in the write transaction. Zero expires immediately. Expired entries
  read as missing. Expiration uses the wall clock so independent processes agree.

`get` returns the caller's `default` unchanged for a missing or expired key.
Stored JSON `null` returns `None`; pass a unique sentinel to distinguish it from
a miss. `set` replaces a key. `delete` reports whether a stored row was removed;
`clear` removes this namespace and returns the number of stored rows removed.
These counts include expired rows still awaiting cleanup. Neither operation
affects another namespace.

`get_many` returns live hits only, retaining the first input order for duplicate
keys. Cached `None` remains present; missing keys are omitted. One read snapshot
and expiry time cover all queries, including batches larger than SQLite's
parameter limit. Every key is validated before I/O.

`set_many` prepares the entire mapping before taking locks, then publishes it in
one transaction with a shared expiry time. An invalid later value or database
failure leaves the whole batch unpublished. Mapping order determines FIFO order;
cleanup and capacity enforcement run once per batch. The methods materialize the
requested batch; callers choose a batch size that fits their memory budget. Empty
batches perform no database I/O.

`get_or_compute` returns a live entry immediately. A miss acquires its key's file
lock, briefly preflights the writer and rechecks the key, then releases the SQLite
transaction before calling `factory`. It encodes the result and commits it in a
second short transaction while retaining the key lock. TTL begins after the
producer completes. Producer or encoding failures publish nothing, and a later
call can retry. Publication errors also propagate; producers should be safe to
repeat after any failure.

## Typed artifacts

The default `get_or_compute` path accepts and returns JSON. Supplying a structural
`codec` allows a producer to return a typed artifact directly:

```text
codec.encode(value: T) -> bytes
codec.decode(raw: bytes) -> T
cache.get_or_compute(key, factory, *, codec=codec, ttl=None) -> T
```

Ordinary bound methods and paired callable attributes are supported, including
reusable serialization codecs. No wrapper class or additional runtime dependency
is required. This shape serves deterministic preprocessing artifacts without
converting large numeric or token batches into intermediate JSON object trees.

The decoder must validate stored bytes and return an independent value. Both
cold and warm results pass through it; cold decode rejection publishes nothing.
An encoder must return `bytes`. Malformed warm artifacts and decoder exceptions
propagate without silently becoming misses or running the producer again.

Every reader of a key must use the same codec. Keep codec artifacts in a dedicated
versioned namespace, and include the complete input and transformation identity
in the key. Change that identity whenever the codec or preprocessing semantics
change. JSON `get`, `get_many`, `set`, and `set_many` remain JSON-only; use
`get_or_compute(..., codec=...)` for typed reads and fills. `delete` and `clear`
still remove the corresponding stored rows regardless of encoding.

## JSON value contract

Values are data, not Python object graphs. Each read decodes an independent
JSON value: mutating a returned list or dictionary cannot alter persisted data.
The first `get_or_compute` result follows the same normalization as later reads.
Tuples become lists, supported non-string mapping keys become JSON strings, and
Python class identity and shared-reference identity are not preserved. Nonfinite
floating-point numbers are rejected. Use JSON-compatible values; this cache
does not load pickles or execute deserialization hooks.

## Coordination and storage limits

Each operation opens and closes its own connection. An instance holds no live
connection and needs no `close()` or context manager. Multiple instances,
threads, and local worker processes may use the same database.

Cold producers run concurrently when their keys use different lock stripes.
Warm readers and unrelated writes continue while those producers work; only
short SQLite transactions serialize. Same-key fills, replacements and deletions
share a lock. `clear` fences all stripes before deleting its namespace, preventing
an in-flight producer from republishing afterward. It also temporarily waits for
fills in other namespaces because the lock stripes belong to the database.

The lock protocol hashes length-framed namespace/key pairs into 64 stable file
locks. Collisions serialize some unrelated keys. Files live beneath a sibling
`.unstd-locks` directory, with at most 64 files per canonical database path.
New directories request mode 0700 and files 0600. Keep these files in place while
any process uses the database; deleting a lock file could admit two owners.
Symlink aliases resolve to the same lock identity. Hardlink aliases and network
filesystems are outside the storage contract. Concurrent processes must use the
same coordination protocol; stop older processes before upgrading.
Each cache handle prepares its canonical path, lock identity, and database-file
hash once at construction; hot calls reuse them without repeated resolution.

A producer may read warm dependencies but must not mutate or start another cold
fill in the same database. Such reentry raises `RuntimeError` before waiting.
Idle handles remain usable after a fork. Child processes close inherited lock
descriptors without unlocking the parent, and a producer returning in a forked
child cannot publish its inherited result.

Coordination applies while the stored value remains valid. A process crash,
rollback, eviction, explicit deletion, or expiry can cause recomputation. Do not
use the producer as an exactly-once guarantee for external side effects.

Use a dedicated database file on local storage. Network filesystems and SQLite
in-memory databases are outside this contract. `max_entries` bounds row count,
not byte usage or database-file size; SQLite can retain freed pages for reuse.
The cache does not perform background maintenance or delete its database files.

## Source and verification

[`__init__.py`](__init__.py) owns the public class, batch snapshots, JSON
normalization, short SQLite transactions, expiration, and retention.
[`locking.py`](locking.py) owns bounded cross-process file locks and fork cleanup.
Tests live under
[`tests/suites/product/cache`](../../../tests/suites/product/cache).

```console
uv run --no-sync pytest tests/suites/product/cache -q
uv run --no-sync python -m bench.speeds check --group cache
```

The operation registry covers construction, individual and batch reads/writes,
deletion, clearing, and warm/cold JSON and typed computation. The [evidence guide](../../../bench/reports/cache/README.md)
includes paired comparisons against the first implementation and a source-backed
generic corpus-loader proof. Timing depends on the host, data and storage.
The [typed operation contracts](../../../bench/reports/cache/codec-operations.md)
separately exercise codec hits and fills without changing earlier ceilings.
