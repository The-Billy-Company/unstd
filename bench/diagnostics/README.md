---
doc_radar:
  sentinels:
    - file: bench/diagnostics/gzip_reads.py
      contains: ['"diagnostic_only": True', 'def compare(', 'def worker(']
---

# Performance diagnostics

Focused crypto, RNG, TLS and runtime investigations live here. Run `python -m bench.diagnostics.NAME --help`; earlier `python -m bench.NAME` entry points still work. Findings belong in [`../audits/`](../audits/README.md); measurements belong in [`../reports/`](../reports/README.md).

See the [benchmark guide](../README.md).

We compare the original gzip read fixtures and their native inflater with
`python -m bench.diagnostics.gzip_reads run --before-install PATH --current-install PATH --output FILE`.
The manual performance workflow can build both installations and retain this
bounded comparison on Linux with its `gzip_diagnostic` option.

The clone follow-up uses [`clone_algorithms.py`](clone_algorithms.py) to compare
public Python entrypoints with specialized controls and full serialization
roundtrips, retaining failed contract checks rather than counting them as wins.
[`clone_allocations.py`](clone_allocations.py) optionally compares public clone
calls with empty output-node allocation using the separately built
[`clone_alloc_floor.c`](clone_alloc_floor.c) diagnostic. That favorable allocation
control excludes traversal, payload references and populated hash tables; it
is not a formal lower bound or a runtime clone implementation. Reproduction and
measurements are in the [common-clone report](../reports/clone/clone-common-cpu-results.md).
Add `--consumer-cases --include 'consumer-*' --gc-enabled` to the allocation
driver for model and slotted-record controls. It reports exact record allocation,
fresh shallow state, and empty C node allocation separately; none copies a full
payload or qualifies as a clone, and beating those measured controls is not a
formal impossibility proof.
[`clone_cold.py`](clone_cold.py) complements warm batches with public-operation
latency after clearing class-admission and field caches outside each timer;
imports, interpreter and allocator remain warm.
