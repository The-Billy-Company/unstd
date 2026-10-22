# Recorded chaos campaigns

The executable chaos lane runs repeated faults against real unstd operations,
then checks committed state, exception identity, handle ownership and recovery.
It occupies Billy's `assurance/adversarial/chaos` leaf and remains an explicit
lane, excluded from the ordinary pytest correctness run.

```console
uv sync --locked --all-extras --group dev
uv run --no-sync python -m tests.tooling.chaos list
uv run --no-sync python -m tests.tooling.chaos check
uv run --no-sync python -m tests.tooling.chaos run --backend installed --seed 173 --artifact-dir /tmp/unstd-chaos-installed
uv run --no-sync python -m tests.tooling.chaos run --backend stdlib --seed 173 --artifact-dir /tmp/unstd-chaos-base
uv run --no-sync python -m tests.tooling run chaos -- --chaos-seed 173 -q
```

The standalone runner is the hard outer deadline for a whole campaign. Direct
pytest selection retains the scenarios' own bounded worker deadlines; use the
standalone command or CI for full process-group supervision. Neither command
needs an external network service, privileged disk fault injector or production
credential.

## Executed groups

| Group | Fault schedule and independent checks |
| --- | --- |
| `fs_process` | Shuffled repeated atomic-write failures at open, fdopen, chmod, partial write/ENOSPC, file sync, replace, stream close, rollback unlink and directory durability seams. Real files are compared with a separate committed-state model; descriptors, modes, neighbor data and subsequent successful writes are checked. Ten writer kills handshake at actual write/sync/replace checkpoints. Repeated process histories exercise both pipe directions, nonzero status, early stdin close, timeout and real SIGINT, with direct-child reaping and recovery. |
| `streams` | Scripted real text/binary handles inject short reads/writes, transport errors, interrupted reads, callback cancellation, seek/flush/finalization faults, and repeated NDJSON restart schedules. Assertions retain exact partial progress and errors, caller-owned handles, independent JSON/gzip interpretation, and successful fresh streams after faults. |
| `clock_entropy` | Isolated frozen/backward/forward clock schedules, UUID counter exhaustion and reseeding, entropy provider exceptions at seed/tail boundaries, exact short-provider behavior for stdlib aliases, process deadlines independent of wall-clock jumps, failed scratch-name entropy, and keyed-token operations while clock/entropy providers are unavailable. Exact layout/vector checks and fixed recovery bytes avoid probabilistic assertions. |

Every scenario journals its concrete schedule and observations. Positive
controls distinguish an unexercised fault from successful recovery, and broken
behavior canaries prove that state corruption, hidden failures or unreaped
children are detected. Errors from injected providers are tested at the real
module seam rather than replacing the program with a canned result.

## Seeds and replay

`ordered()` derives a local RNG from the versioned seed and scenario namespace.
It never changes global random state or uses Python's randomized hash. Fixed
fault profiles remain reviewed code. A campaign's first round uses `--seed`;
round `i` uses `(seed + i) % 2**64`. `--rounds` accepts 1..100.

```console
uv run --no-sync python -m tests.tooling.chaos run --group fs_process --seed 4294967297 --rounds 10 --artifact-dir /tmp/unstd-chaos-soak
uv run --no-sync python -m tests.tooling.chaos replay /tmp/unstd-chaos-soak/round-000/fs_process/installed/report.json --artifact-dir /tmp/unstd-chaos-replay
```

Replay admits only a current registered group, an unsigned 64-bit seed, a known
backend, schedule version and valid limits. It reruns that whole group, including
its success/recovery controls. It does not execute arbitrary code or paths from
the report. Source hashes identify the implementation and reviewed profiles;
replaying after a correction intentionally tests the current checkout. PIDs,
durations and fresh temporary paths change across runs; the ordered faults do
not change when the profiles and schedule version stay fixed.

## Execution and evidence

The default per-group limits are 120 seconds wall time, 45 seconds per test,
60 seconds POSIX CPU and 2,048 MiB Linux address space. Override them with
`--wall-seconds`, `--per-test-seconds`, `--cpu-seconds` and `--memory-mb`.
Limits are independent per group and round, not a claimed total process-tree
CPU or resident-memory quota. Darwin does not claim a reliable address-space
bound. Reports state actual enforcement.

Each group gets a fresh isolated interpreter pinned to the checkout. Installed
workers require every declared extra. Stdlib workers block the relevant optional
production backend imports before product loading and propagate that policy into
nested scenario workers. Optional-only operations skip explicitly in that lane;
an entirely skipped or empty group cannot pass. Every passing case must have a
nonempty journal and complete setup/call/teardown evidence.

The outer parent owns a POSIX process group. Shared test-worker helpers keep
their nested children in that group during campaigns; local supervisors reap
their own children, and the outer parent kills remaining group members on every
exit. Standalone direct helper usage retains its own process-group ownership.
Non-POSIX platforms report their narrower cleanup capability; the current CI
campaign matrix is Linux and macOS.

Reports retain seed, schedule/source hashes, Python/platform, backend/control
admission, actual case counts, skip reasons, journals, bounded diagnostics and
the active test on interruption. Each journal update is also written atomically
as the experiment runs, so a later native abort or watchdog kill retains earlier
planned faults and observed steps without relying on fixture teardown.
Journal size, captured output and worker
evidence reads have explicit bounds. Assertions, unexpected exits, resource
limits, missing journals, incomplete runs and watchdog expiry fail; they do not
inflate successful fault counts. Reports live in a unique ignored `.local/chaos`
directory by default, or under `--artifact-dir`.

## CI and scope

[`chaos.yml`](../../../.github/workflows/chaos.yml) has four named jobs:
`Fault injection / Linux / installed`, `Linux / stdlib`, `macOS / installed` and
`macOS / stdlib`. Each job installs dependencies once, then `--group all`
discovers and runs every executable group. New groups join automatically without
adding another setup job. Pull requests, main pushes and manual runs execute one
round per group; nightly jobs execute ten recorded rounds. Independent per-group
worker bounds remain unchanged. The 65-minute outer timeout accommodates three
groups × ten rounds × 120 seconds plus setup, cleanup and evidence upload.

Each worker has a folded log section. The GitHub job summary lists each group,
seed, result, case counts, duration and replay-report path inside the artifact.
All reports and journals upload after success or failure and remain for fourteen
days. Dependencies are cached by the lockfile and project configuration; unstd
is rebuilt from the current checkout. Actions are pinned and repository
permissions are read-only. Ordinary CI validates discovery and runs the harness
gates; campaign scenarios stay explicitly selected.

These tests inject syscall failures around real files; they do not exhaust the
host disk or prove physical power-loss durability. SIGKILL experiments prove
process interruption and the actual atomic commit boundary. Entropy experiments
prove wrapper/provider behavior, not CSPRNG quality or cryptographic strength.

An interrupted gzip sink can poison the current member. The dedicated EINTR
experiment records the running interpreter/backend's actual result, including
an observed stdlib retry that acknowledges input but leaves a CRC-invalid member.
It verifies the injected fault and independent decompression outcome, then
requires successful recovery using a fresh member. The facade does not promise
in-place continuation after such a sink fault; discard that member. Other
transport failures retain strict exception/progress/ownership assertions.
