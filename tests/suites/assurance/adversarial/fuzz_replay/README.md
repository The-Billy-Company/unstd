# fuzz-replay

Status: **active**. Every committed parser/token input runs its real assertions
in a bounded fresh interpreter for each supported installed/stdlib mode.
Manifests authenticate input hashes, sizes, envelopes and reviewed provenance.

```console
uv run --no-sync python -m tests.tooling run fuzz-replay -- -q
uv run --no-sync pytest --require-extras -q -m fuzz_replay
```

Each target/backend worker has 20 seconds of wall time, 10 seconds of POSIX CPU,
3 seconds per input and a 1,024-MiB Linux address-space limit. Workers admit no
input above 65,536 bytes. Installed capabilities skip explicitly only in optional
local environments; `--require-extras` makes missing extras fail. Stdlib workers
block optional imports before loading targets. Timeouts, unexpected exceptions,
crashes, incomplete case counts and abnormal exits fail replay.

The [fuzz runbook](../../../../tooling/fuzz/README.md) documents standalone
deterministic mutation, genuine Atheris campaigns, exact reproduction and corpus
promotion. Replay is finite correctness evidence; it collects no coverage.
