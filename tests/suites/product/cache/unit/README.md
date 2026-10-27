# Cache unit contracts

Persistent round trips, JSON normalization and rejection, independent decoded
values, key/namespace isolation, expiry boundaries, FIFO limits, and producer
rollback. Time is controlled directly rather than inferred from sleeps.

`test_batch.py` additionally checks atomic whole-batch failure, a consistent
snapshot across query chunks during a concurrent commit, shared expiry, and
ordered FIFO retention. `test_locking.py` checks bounded files, partial-failure
cleanup, one acquisition deadline, fork ownership, and the Windows adapter.
