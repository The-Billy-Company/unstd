# Test and release gates

These tests check the machinery used to make product claims: suite ownership,
backend admission, CI grouping, package descriptions, benchmark evidence,
campaign containment, dependency policy, and release verification. All descendants
share the active `gates` inventory owner.

| Directory | Responsibility |
| --- | --- |
| [bench](bench/README.md) | Timing, baseline, comparison, and native-dispatch evidence |
| [chaos](chaos/README.md) | Fault schedules, worker supervision, replay, and CI campaigns |
| [concurrency](concurrency/README.md) | Concurrency campaign selection, histories, and lane isolation |
| [fuzz](fuzz/README.md) | Corpus authentication, target laws, mutation, and bounded execution |
| [packaging](packaging/README.md) | Published package descriptions and lazy import/version contracts |
| [release](release/README.md) | Distribution validation and publication manifest integrity |
| [resources](resources/README.md) | Resource budgets, measurements, failure controls, and cleanup |
| [supply_chain](supply_chain/README.md) | Lock policy, advisory evidence, SBOMs, and release workflow wiring |

Tests directly in this directory verify the inventory/runner, CI groups and
workflow, required-extra behavior, and optional benchmark collection. From the
repository root, run `uv run --no-sync python -m tests.tooling run gates -- -q`.
The normal run includes these gates but excludes the `perf` calibration tests.
