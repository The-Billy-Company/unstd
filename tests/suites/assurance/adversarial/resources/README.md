# Resource contracts

Status: **active**. Finite probes run through the existing suite runner; there is
no separate campaign framework. Every invocation gets a fresh killable worker.

Test these first, in risk order:

| Priority | Contract | Evidence |
| --- | --- | --- |
| P0 | NDJSON read/write stay streaming | 1,000 versus 10,000 unique rows; exact values/checksums against stdlib JSON, one-row consumption, fixed allocation allowance. Correct-output eager controls fail the same bound. |
| P0 | Repeated writes and rollback release OS resources | 64 successful writes, 64 body failures and 64 early-closed directory walks; unchanged prior contents on failure, exact descriptor counts and zero scratch files. |
| P1 | WAV blocks bound memory and release owned handles | 32,768 versus 327,680 independently generated PCM frames; exact samples/checksum, bounded block size, real OS descriptors after early close, borrowed file remains usable. |
| P1 | Cache churn respects capacity and object lifetime | 2,048 accepted/declined regex compiles with the documented 512-entry capacity and purge; 128 generated classes per clone operation collect like stdlib controls. |
| P1 | Deep JSON completes or rejects safely | Successful shallow control, 20,000 nested arrays, interpreter error parity or iterative verification of the complete shape. |

Each streaming probe must stay below 2 MiB of traced peak working allocations,
and the larger input may add at most 128 KiB. Imports happen before tracing;
fixtures are generated incrementally. Tracemalloc is a Python-allocation proxy,
not a complete accounting of native heaps. Peak RSS is recorded separately.

Every worker has a 15-second parent wall deadline, a 10-second CPU budget and a
512 MiB memory budget. Linux enforces address space with `RLIMIT_AS`. macOS uses
a parent RSS watchdog sampled roughly every 20 ms plus sampling overhead, plus
a peak-RSS check before admitting a completed probe. Both platforms enforce `RLIMIT_CPU`, disable core
dumps, bound stdout/stderr to 64 KiB each, and kill/reap the process group on
every exit. Unsupported containment platforms fail explicitly. These generous
bounds detect exhaustion and growth; they are not speed benchmarks.

`installed` uses available backends. `stdlib` blocks optional imports before
loading the product, using the existing backend policy. Bundled dependency-free
extensions can still run. Local installed runs may use fallbacks; required CI
runs install all extras and use `--require-extras`; worker facade flags must also
confirm that each probe's required backends loaded inside its memory limit.

```console
uv run --no-sync python -m tests.tooling run resources -- -q
uv run --no-sync python -m tests.tooling run resources -- --require-extras -q --junitxml=.local/resources.xml
uv run --no-sync pytest tests/suites/assurance/gates/resources -q
```

Reports attached to pytest user properties retain the probe, backend, interpreter,
platform, containment mechanism, elapsed/CPU time, RSS, traced allocations and
contract observations. JUnit retains these reports; failed workers include bounded
stderr and exit status. The ordinary correctness run excludes this lane. CI runs
it explicitly on Linux and macOS with both backend policies.

The [harness gates](../../gates/resources/test_harness.py) exercise real success,
failure and exhaustion controls. The [coverage plan](../../../../COVERAGE.md)
still reserves sustained soak runs, digest streaming, text-matrix allocation,
larger parser corpora, Windows handles and stable performance baselines for later.
