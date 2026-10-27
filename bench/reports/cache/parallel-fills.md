# Independent producers share a database without serializing their work

[`parallel-fills.json`](parallel-fills.json) compares the frozen first cache
implementation with the upgraded implementation on eight worker processes.
Each producer computes a genuine 300,000-iteration PBKDF2-HMAC-SHA256 derivation.
There are no sleeps in the producer. Keys deliberately select eight distinct
lock stripes to isolate independent-key behavior.

| Three paired rounds, median wall time | First implementation | Current implementation |
| --- | --- | --- |
| Eight completed cold fills | 304.40 ms | 66.96 ms |
| Peak simultaneously executing producers, every round | 1 | 8 |

The observed elapsed-time improvement is **4.55×** on this macOS arm64 worker.
Each variant starts fresh processes and a fresh database. Workers import and
initialize before reaching a parent-owned start barrier. Only after every
worker is ready does the parent start the timer and release them. The timed
region includes dispatch, lookup, real producer CPU work, commit, and worker
exit. Process startup and independent reference derivations are outside timing.
Execution order alternates between the three pairs.

Every child records the exact monotonic start/end of its producer. The parent
checks one invocation per worker, distinct process IDs, exact expected derived
values, and a second read of all persisted results. It computes peak overlap
from those intervals and requires one for the original implementation and more
than one for the new implementation in every round. The recorded result was
eight in all three new rounds; correctness does not depend on a brittle speed
threshold.

```console
uv run --no-sync python -m bench.profiles.cache_concurrency --workers 8 --rounds 3 --iterations 300000 --output .local/cache-parallel.json
```

[`cache_concurrency.py`](../../profiles/cache_concurrency.py) authenticates the
[frozen source](first-version.py.gz) before importing it independently. It retains
source hashes for both versions, current locking, and the driver, plus every
raw worker interval and output. Owned children are killed and reaped on errors,
including a failure while starting a later worker.

This is a synthetic CPU workload, distinct from the source-backed Consumer loader
proof. It establishes that the former database-wide producer bottleneck is
removed; speed depends on CPU availability, storage, work distribution, and
stripe collisions. Same-key or same-stripe producers still serialize. Product
integration tests use explicit parent/child handshakes to prove independent
production and unrelated write progress without timing assumptions.
