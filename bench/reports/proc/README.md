# Process exit latency on Linux and macOS

Measured 2026-10-25 against the previous `proc` implementation at
`6ef26acd9156bc12a2731f8651ab3a7dbcff7391`. Kernel exit notifications reduce
complete-call latency by **23–27%** for children that close captured pipes before
exiting. This is a measured improvement on both platforms, with no optional
dependency or public API change.

## Implementation

The private [`Popen` backend](../../../src/unstd/proc/_internal/backend.py)
overrides bounded `_wait` calls. It first checks whether the child has already
exited. A running child uses a Linux pidfd with `poll`, or macOS kqueue with
`NOTE_EXIT`. Readiness only observes exit; stdlib still reaps the child and
interprets status under its waitpid lock. Each notification descriptor is
non-inheritable and closes on success, timeout, setup failure and interruption.

Unsupported facilities, old kernels, restricted syscalls and descriptor
allocation failures fall back to the stdlib wait with the remaining deadline.
Windows retains its stdlib wait. Process creation, descriptor isolation,
communication, universal-newline decoding, timeout output and SIGINT grace stay
with `subprocess`; no global monkey patch, extra thread or signal handler is
installed. The public runner retains explicit kill-and-reap cleanup.

## Paired results

Times are median milliseconds per complete public call across nine alternating
baseline/current worker pairs. Speedup is baseline time divided by current
time. Both implementations run under the same interpreter on each platform.

| Workload | macOS before → after (ms) | macOS speedup | Linux before → after (ms) | Linux speedup |
| --- | ---: | ---: | ---: | ---: |
| Empty executable | 2.592 → 2.740 | 0.95× | 0.222 → 0.231 | 0.96× |
| `run_ok`, empty executable | 2.536 → 2.575 | 0.98× | 0.216 → 0.217 | 1.00× |
| Short stdout and stderr | 11.687 → 11.879 | 0.98× | 4.756 → 4.595 | 1.04× |
| Close pipes, then sleep 2 ms | 19.817 → 15.165 | **1.31×** | 10.856 → 8.037 | **1.35×** |
| Close pipes, then sleep 20 ms | 49.313 → 37.288 | **1.32×** | 39.778 → 29.062 | **1.37×** |
| cwd, environment and stdin | 14.171 → 15.189 | 0.93× | 5.621 → 5.385 | 1.04× |
| `capture`, short stdout | 11.414 → 11.708 | 0.97× | 4.520 → 4.476 | 1.01× |
| 1 MiB stdout | 13.352 → 12.723 | 1.05× | 5.885 → 5.803 | 1.01× |
| 16 MiB stdout | 33.047 → 32.129 | 1.03× | 19.221 → 19.171 | 1.00× |
| 1 MiB stderr | 13.673 → 14.291 | 0.96× | 5.773 → 5.887 | 0.98× |
| Simultaneous 1 MiB stdin/stdout/stderr | 22.762 → 22.649 | 1.00× | 10.377 → 9.963 | 1.04× |

The targeted cases save **4.65–12.03 ms on macOS** and **2.82–10.72 ms on Linux**.
Untimed profiles show 2–3 polling sleeps for the original 2 ms case and five for
the 20 ms case. Every candidate profile instead uses the platform's native
notification and records zero sleeps in all nine rounds.

Ordinary commands and large I/O show no consistent gain: their measured median
ratios span 0.93–1.05× on macOS and 0.96–1.04× on Linux, including observed
slowdowns. Raw paired samples retain substantial host variability; this report
does not establish a universal speedup or a regression-free latency bound.
Startup and the child program's work still dominate many calls. Parent CPU
samples are retained separately and exclude child CPU; these are wall-latency
claims, not CPU-throughput claims.

## Evidence and reproduction

- [macOS raw evidence](macos-py314.json): macOS 26.6 arm64, CPython 3.14.9.
- [Linux raw evidence](linux-py314.json): Linux arm64, glibc 2.41, CPython 3.14.8,
  in a dedicated Docker Desktop container on the same Mac. This is a real Linux
  kernel run in a VM, not bare-metal qualification or an OS-to-OS comparison.

The standalone [driver](../../profiles/proc_backend.py) selects each source in
a fresh worker before importing `unstd`. It refuses changed public signatures,
checks complete output against independently specified fixtures, and verifies
source fingerprints across rounds. Imports, warmup profiling and output checks
are outside timing. Timed batches include complete calls and retain their
results for exact validation afterward; GC is disabled only during the batch.
All raw wall/parent CPU samples, source hashes and the harness hash are saved.

Extract the original package into a temporary directory, then compare it with
the current source on either platform:

```console
mkdir -p /tmp/proc-before
git archive 6ef26acd9156bc12a2731f8651ab3a7dbcff7391 src/unstd/proc | tar -x -C /tmp/proc-before
python bench/profiles/proc_backend.py --baseline-source /tmp/proc-before/src --source src --rounds 9 --output /tmp/proc-results.json
```

No optional backend is required.
Use `--case closed-pipes-before-exit --case closed-pipes-20ms` for a focused run.

## Validation

The proc and benchmark suites pass **87 tests on each of macOS and Linux** with
Python 3.14; all **77 proc tests** also pass on macOS with Python 3.12.15.
The mixed process fault-history test passes on each platform, and the public
API gate passes 58 tests. Ruff and the focused type checks pass for Python
3.12/3.13/3.14 on Linux, Darwin and Windows targets. Backend tests cover unavailable
native support, ENOSYS/EMFILE/ESRCH fallback, remaining deadlines, handle closure
and non-inheritability, parent descriptor isolation and concurrent children.
Real subprocess tests cover large duplex I/O, partial timeout bytes, strict
codecs, signals, descendant-held pipes and direct-child reaping. Real SIGINT
regressions interrupt both pipe communication and native exit waits through
`run`, `capture` and `run_ok`, preserving exception identity and recovery.

The implementation uses a private stdlib seam; compatibility tests and type
checks remain necessary when adding Python versions. Native setup failures
retain correctness through fallback, but do not retain the latency gain.

```console
uv run --no-sync pytest tests/suites/product/proc tests/suites/assurance/gates/bench/test_proc_backend.py -q
uv run --no-sync pytest tests/suites/assurance/adversarial/chaos/test_fs_process.py -q -m chaos -k process_fault
```
