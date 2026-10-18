# Recorded concurrency campaigns

This active lane follows Billy's `assurance/adversarial/concurrency` organization.
Product expectations live in suites, reusable actor mechanics in support, and
campaign supervision here. It reuses the bounded chaos engine, with separate
discovery, lane admission, fixtures, reports and replay validation.

```console
uv sync --locked --all-extras --group dev
uv run --no-sync python -m tests.tooling.concurrency list
uv run --no-sync python -m tests.tooling.concurrency check
uv run --no-sync python -m tests.tooling.concurrency run --backend installed --seed 173 --artifact-dir /tmp/unstd-concurrency-installed
uv run --no-sync python -m tests.tooling.concurrency run --backend stdlib --seed 173 --artifact-dir /tmp/unstd-concurrency-base
uv run --no-sync python -m tests.tooling run concurrency -- --concurrency-seed 173 -q
```

Default pytest excludes this lane. Direct pytest selection retains each
scenario's own worker bounds; the standalone command adds hard outer deadlines
and process-group supervision. Ordinary owning regressions pin confirmed UUID
fork-lock and regex cache-capacity defects without requiring an explicit campaign.

## Executable groups

| Group | Schedules and independent expectations |
| --- | --- |
| `filesystem` | Three writers and two readers overlap in threads and spawned processes. Binary/Unicode writes, durable/non-durable commits and missing/existing destinations are compared with a separate whole-generation model. Readers pinned before replacement must finish the old generation; fresh opens see the complete new generation. Mixed body/replace failures preserve committed state, modes, neighboring data and descriptor ownership. Scratch-name collisions and deliberately corrupted commit histories exercise rejection controls. |
| `ids` | Frozen/rollback clocks, 2/4/6 contending threads, counter rollover and fixed entropy produce independently calculated UUID bits. Invocation/response intervals and per-thread order constrain valid reservation histories without assuming completion order. Separate public/native calls observe shape and finite uniqueness. Spawned children have independent fixed state; a real fork while another thread holds the fallback lock must initialize an unlocked child counter and leave the parent unchanged. A poisoned child hook proves the deadlock watchdog detects the original failure. |
| `shared_state` | Cold clone admission, dataclass field caching and copy-hook reentry; overlapping regex compilation/eviction, retained matches across purge, nested callbacks and callback failures; shared typed codecs with independent results and strict/lax policy; interleaved/reentrant YAML loads and failure recovery; collision-heavy persistent-map branches. Each scenario has a deliberately broken-result control. |

The library's guarantees determine each oracle. The suite does not share a
mutable parser instance that the API never promises to make safe. Immutable
historical maps must stay unchanged when branches evolve; clone outputs must
have independent mutable state while preserving their internal aliases. Native
UUID samples establish observed uniqueness and layout, not statistical collision
resistance or an undocumented monotonicity guarantee.

## Schedules and replay

The versioned `ordered()` helper derives a local RNG from an unsigned 64-bit seed
and scenario namespace, independently of Python hash randomization and global
random state. Barriers, events and handshakes establish overlap. Bounded timeouts
detect a failed schedule; sleeps do not choose its outcome. Round `i` uses
`(seed + i) % 2**64`; `--rounds` accepts 1..100.

```console
uv run --no-sync python -m tests.tooling.concurrency run --group ids --seed 4294967297 --rounds 10 --artifact-dir /tmp/unstd-concurrency-rounds
uv run --no-sync python -m tests.tooling.concurrency replay /tmp/unstd-concurrency-rounds/round-000/ids/installed/report.json --artifact-dir /tmp/unstd-concurrency-replay
```

Replay accepts a registered group, matching lane, seed, schedule version,
backend and valid limits. It runs the current reviewed group, including success
and corruption controls. Source hashes identify the implementation tested; replay
does not execute arbitrary report paths or code. The planned schedule is stable;
OS interleavings, PIDs, durations and temporary paths may change. Histories are
validated against the same permitted outcomes instead of requiring identical
completion order. Chaos reports are rejected by concurrency replay and vice versa.

## Bounds and evidence

Each group/round defaults to 120 seconds wall time, 45 seconds per case,
60 seconds POSIX CPU and 2,048 MiB Linux address space. Options are
`--wall-seconds`, `--per-test-seconds`, `--cpu-seconds` and `--memory-mb`.
The report states actual enforcement; CPU/address-space limits apply to each
worker rather than a total tree quota. Darwin does not claim a reliable
address-space cap. Scenario helpers also bound actor joins, reap owned processes
and close descriptors on failure.

Workers run in fresh interpreters pinned to the checkout. Installed campaigns
require all declared extras. Stdlib campaigns block relevant optional production
backends before imports, including in nested workers. Native-only UUID,
msgspec, YAML and immutable-map scenarios skip explicitly when blocked. An empty
or entirely skipped group fails admission. Every passing case must produce a
nonempty journal and complete setup/call/teardown evidence.
Workers discard inherited pytest selection and explicit plugin settings so a
local `PYTEST_ADDOPTS` cannot turn a full required group into a passing subset.

The POSIX parent owns the campaign process group and kills remaining members on
every exit. Nested helpers keep children inside that group and reap their own
actors. Non-POSIX reports expose their narrower cleanup support. Reports retain
lane, seed, schedule/source hashes, interpreter/platform/backend admission,
counts, skip reasons, active test, bounded output and observed histories.
Journals are persisted atomically during execution so a later watchdog or native
abort retains earlier progress. Missing/oversized evidence, incomplete runs,
assertions, crashes and deadline/resource failures cannot pass. The default
output is a unique ignored `.local/concurrency` directory.

## CI and limits of the evidence

[`concurrency.yml`](../../../.github/workflows/concurrency.yml) derives three
groups × installed/stdlib × Linux/macOS from executable discovery. Pull requests,
main pushes and manual runs execute one round; nightly runs execute ten. Jobs
have a 30-minute outer timeout and retain successful/failing artifacts for
fourteen days. Actions are pinned and permissions read-only. Ordinary CI checks
discovery and runs campaign/selection/replay/CI gates alongside the shared
engine's watchdog, crash, cleanup and resource-control gates.

These are controlled finite histories on the tested builds. They do not establish
free-threaded Python support, Windows handle semantics, arbitrary shared mutable
object safety, cryptographic strength or physical power-loss durability. Resource
soak, instrumented native fuzzing and stable-worker performance remain separate
lanes in the [coverage plan](../../COVERAGE.md).
