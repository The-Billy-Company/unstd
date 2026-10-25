# Private native implementations

Build inputs live here; the installed Python API lives under
[`src/unstd/`](../src/unstd/README.md). Python wrappers select these optional
kernels behind their existing private extension names. C, headers, Rust sources,
and qualification evidence are shipped in the source archive, outside the
installed Python package.

| Directory | Responsibility |
| --- | --- |
| [`clone/`](clone/README.md) | Exact-type structural cloning kernel |
| [`crypto/`](crypto/README.md) | Bounded hash-stream absorption kernel |
| [`serde/`](serde/README.md) | Guarded JSON encode, decode, and line kernels |
| [`text/`](text/README.md) | C fuzzy metrics and the opt-in Rust prototype |
| [`compress/`](compress/README.md) | Gzip adapter, inflater, authenticated vendors, and qualification |
| [`audio/`](audio/README.md) | Reviewed external audio-runtime patches and build provenance |

[`hatch_build.py`](../hatch_build.py) owns compiler selection, portable fallbacks,
editable extension cleanup, and wheel inclusion. Default builds require only a
C compiler; unavailable optional compilers preserve Python fallbacks. Release
builds require every declared native module to compile. Cargo runs only when
`UNSTD_TEXT_RUST=1` explicitly selects the text prototype.

Vendored gzip source retains its upstream directory structure, licenses, and
per-file provenance manifests under [`compress/vendor/`](compress/vendor/README.md).
Generated compiler and Cargo outputs are ignored local artifacts.
