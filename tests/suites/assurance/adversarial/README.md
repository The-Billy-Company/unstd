# Adversarial suites

These active suites exercise cross-module behavior under hostile inputs, faults,
contention, and resource pressure. Reusable workers live in
[support](../../../support/README.md); standalone campaigns live in
[tooling](../../../tooling/README.md).

| Directory | Evidence | Selection |
| --- | --- | --- |
| [chaos](chaos/README.md) | Recorded filesystem/process, stream, clock, and entropy failures with recovery controls | `chaos` |
| [concurrency](concurrency/README.md) | Gated filesystem, UUID, cache, and shared-state histories | `concurrency` |
| [fuzz_replay](fuzz_replay/README.md) | Finite authenticated corpus replay in bounded backend workers | `fuzz-replay` |
| [resources](resources/README.md) | Streaming memory, cleanup, cache lifetime, and JSON depth bounds | `resources` |

Run a lane from the repository root with
`uv run --no-sync python -m tests.tooling run LANE -- -q`.
Only corpus replay runs under the default pytest selection; the other lanes
require explicit selection. Confirmed module defects also belong in the owning
product regression suite.
