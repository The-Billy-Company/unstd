# Regex regressions

Status: **active**. The surrogate-region finding retains its exact and minimized
fuzz inputs, together with direct stdlib comparisons for delayed match
projections, original subject identity, bounded regions and callback histories.

An encodable-Unicode success control also proves that supported subjects still
use native matching. Run this lane with
`uv run --no-sync python -m tests.tooling run regression -- -q`.
See the [fuzz runbook](../../../../tooling/fuzz/README.md) for exact-input replay
and corpus promotion.
