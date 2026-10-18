# Standalone mutation tooling

Reserved for mutation-test configuration and result interpretation. This
directory currently contains design guidance only. There is no executable
mutation job, installed mutation dependency, mutation threshold, or claim about
the suite's mutation score.

Mutation testing changes implementation behavior and asks whether existing tests
detect the change. It evaluates assertion strength; it does not replace contract,
property, integration, security, or packaging tests.

## Scope and rollout

1. Start with a small explicit allowlist of critical pure helpers whose behavior
   already has substantive assertions: format/dtype normalization, numeric
   boundary and quantization logic, encoding policy, parser/serializer guards,
   and other deterministic transformations. Confirm the chosen tests detect
   changed values, boundaries, errors, and side effects where applicable.
2. Expand to crypto validation guards only after independent vector, tamper,
   malformed-input, and fail-closed regressions exist. Mutation results cannot
   establish cryptographic strength or constant-time execution.
3. Expand to filesystem/process cleanup only after real resource-lifecycle and
   failure-path assertions exist: correct cleanup after partial failure,
   interrupted work, timeout/cancellation, and ownership changes as applicable.
   Mutants must execute in disposable local resources with bounded subprocesses.

These are candidate areas, not an assertion that all have sufficient coverage
today. Select exact source functions/modules and the tests that exercise their
contracts before enabling each campaign. Do not mutate dependency code, vendored
cryptographic primitives, generated files, tests, or standalone fuzz targets by
default. Document every source exclusion and its reason.

## Separate job and interpretation

Run mutation analysis as a separate, targeted job after the ordinary selected
tests pass. Give it explicit source and test allowlists, worker/time limits,
backend configuration, and a pinned tooling environment. Begin with manual
campaigns; scheduling and blocking release gates follow only after its runtime
and failure classifications are understood.

Report killed, survived, timed-out, invalid, and untested mutants separately.
Inspect survivors for missing behavior assertions, equivalent mutations, or
out-of-scope changes. Timeouts and infrastructure failures must not silently
inflate the killed count. Retain the exact tool configuration, source revision,
Python/backend versions, selected tests, budgets, mutant diff, and outcome so a
result can be reproduced. Keep generated mutant trees and reports in job
artifacts outside the tracked source tree.

Establish a reviewed baseline for each targeted scope before choosing a
threshold. Set any future threshold per critical scope with a documented
denominator and exception policy; no arbitrary global percentage is prescribed
here. Add regression assertions for meaningful survivors before broadening the
source allowlist. Reassess cached results after test, dependency, configuration,
or fixture changes.

## Candidate engine and platform constraints

[mutmut](https://github.com/boxed/mutmut) is a candidate Python mutation engine.
Its upstream process model requires POSIX fork support; worker processes can
inherit unsafe state from threads, event loops, monkeypatches, open connections,
or native runtimes. Choose a compatible pinned release and verify its supported
isolation options before adopting them. Prefer a dedicated Linux worker for a
repeatable campaign and investigate hangs or inconsistent results independently
of the source mutation.

Do not add an engine, dependency, command wrapper, CI job, or numerical gate
until a real targeted campaign and its substantive regression assertions are
implemented and reviewed.
