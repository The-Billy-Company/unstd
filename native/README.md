# Private native implementations

Build inputs for private CPython extensions; public APIs and fallback policy
live under [`src/unstd/`](../src/unstd/README.md). Native sources ship in the
sdist, outside the installed Python package.

| Directory | Responsibility |
| --- | --- |
| [`clone/`](clone/README.md) | Exact-type container graphs and admitted dataclass copying |
| [`crypto/`](crypto/README.md) | Hash-stream batching and bearer-envelope parsing |
| [`serde/`](serde/README.md) | Guarded JSON encode, decode, and line kernels |
| [`text/`](text/README.md) | C fuzzy metrics and the opt-in Rust prototype |
| [`compress/`](compress/README.md) | Gzip adapter, inflater, authenticated vendors, and qualification |
| [`audio/`](audio/README.md) | Reviewed external audio-runtime patches and build provenance |

[`hatch_build.py`](../hatch_build.py) attempts five extensions on CPython,
skipping free-threaded builds and other interpreters. Local compiler failures
leave Python fallbacks available; `CIBUILDWHEEL=1` makes those failures fatal.
Default kernels use C; `UNSTD_TEXT_RUST=1` selects the Rust text implementation.
The reviewed gzip archive experiment has separate
[build requirements](compress/experiments/README.md).

Editable builds replace extension files atomically and remove stale module
ABIs; wheel builds include only successful outputs. Compiler and Cargo outputs
are ignored. Vendored gzip sources retain licenses and per-file hashes under
[`compress/vendor/`](compress/vendor/README.md).
