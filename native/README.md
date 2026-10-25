# Private native implementations

Build inputs for private CPython extensions; public APIs and fallback policy
live under [`src/unstd/`](../src/unstd/README.md). Native sources ship in the
sdist, outside the installed Python package.

Each extension starts at `src/adapter.c`; the Rust text alternative starts at
`text/rust/adapter.c`. These files own CPython registration and entry points,
with small kernels inline and larger implementations in role-named C files or
headers. Python module names remain independent of source filenames.

| Source guide | Entry file | Responsibility |
| --- | --- | --- |
| [`clone/`](clone/README.md) | [`adapter.c`](clone/src/adapter.c) | Container graphs and admitted dataclasses |
| [`containers/`](containers/README.md) | [`adapter.c`](containers/src/adapter.c) | Sorted collection operations and positional indexes |
| [`proc/`](proc/README.md) | [`adapter.c`](proc/src/adapter.c), [`pipes.c`](proc/src/pipes.c) | POSIX spawning and captured-output exchange |
| [`crypto/`](crypto/README.md) | [`adapter.c`](crypto/src/adapter.c) | Hash-stream batching and bearer-envelope parsing |
| [`serde/`](serde/README.md) | [`adapter.c`](serde/src/adapter.c) | JSON proofs, codec dispatch and implementation headers |
| [`text/`](text/README.md) | [`adapter.c`](text/src/adapter.c), [`rust/adapter.c`](text/rust/adapter.c) | Levenshtein and Jaro metrics |
| [`compress/`](compress/README.md) | [`adapter.c`](compress/src/adapter.c) | Gzip buffer decoding, stream inflation and checksums |
| [`audio/`](audio/README.md) | External build recipe | Reviewed libsndfile patch and build provenance |

[`hatch_build.py`](../hatch_build.py) attempts seven extensions on CPython,
skipping free-threaded builds and other interpreters. Local compiler failures
leave Python fallbacks available; `CIBUILDWHEEL=1` makes those failures fatal.
Default kernels use C; `UNSTD_TEXT_RUST=1` selects the Rust text implementation.
The reviewed gzip archive experiment has separate
[build requirements](compress/experiments/README.md).

The [Linux atomic-write experiment](fs/experiments/README.md) is built explicitly
for Billy consumer profiling. It is outside the normal build and public fs
dispatch; its admission limits include different cancellation checkpoints.

From the repository root, build a wheel that fails on any native compiler error:

```console
CIBUILDWHEEL=1 uv build --wheel --out-dir .local/native-wheels
```

For editable development, use `uv sync --reinstall-package unstd` with the
extras needed for that domain, then run `uv run --no-sync python -m pytest
tests/suites/product/DOMAIN -q`. A source edit or build-selection variable takes
effect only after rebuilding. Each source guide explains the invariants to
preserve when changing its kernel.

Editable builds replace extension files atomically and remove stale module
ABIs; wheel builds include only successful outputs. Compiler and Cargo outputs
are ignored. Vendored gzip sources retain licenses and per-file hashes under
[`compress/vendor/`](compress/vendor/README.md); their upstream filenames are
part of that authenticated inventory.
