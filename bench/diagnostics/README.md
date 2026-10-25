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
