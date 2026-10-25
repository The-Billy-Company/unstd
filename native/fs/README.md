# Linux filesystem backend

The bundled `unstd.fs._fs_native` extension accelerates complete
[`fs.atomic_write`](../../src/unstd/fs/README.md) calls on ordinary GIL-enabled
Linux CPython. It needs no optional extra or external runtime library.
Other platforms and unsupported build configurations keep Python.

| Directory | Ownership |
| --- | --- |
| [`src/`](src/README.md) | Production C entrypoint, syscall loop, cancellation, errors and cleanup |
| [`qualification/`](qualification/README.md) | Test-only ELF observer for deterministic syscall failures and signals |
| [`experiments/`](experiments/README.md) | Historical standalone prototype and its profiling tools |

Only `src/adapter.c` is compiled into the installed extension. The prototype is
excluded from package builds and remains available for historical comparisons.
The test-only observer must never be loaded during benchmark measurements.

The public wrapper admits exact bytes or default UTF-8 text with an existing
parent and intact OS/path/orchestration bindings. Unsupported inputs, unavailable
native capabilities and missing binaries use the complete Python implementation.
A native I/O exception propagates without retrying through Python. The
[production source guide](src/README.md) specifies ownership before signal
checks, pre-commit rollback, committed-state reporting, mode preservation and
the three durability modes. `atomic_writer` continues to stream through Python.

`hatch_build.py` builds the kernel automatically on supported interpreters.
Local compiler failure leaves the Python fallback usable; `CIBUILDWHEEL=1`
requires successful compilation for release wheels. To rebuild locally:

```console
uv sync --reinstall-package unstd
uv run --no-sync python -m pytest tests/suites/product/fs -q
```

The native integration cases require Linux, the compiled extension and a C
compiler for the observer; other environments skip those cases explicitly.
The [production qualification](../../bench/reports/fs/production/README.md)
records Linux CPython 3.12–3.14 contracts, direct and sdist-rebuilt installed
wheels, compiler-failure fallback, ASan/UBSan, and native/fallback routing for
13 consumer workload shapes. Disk wall time remains variable even where small
writes save CPU; the measurements retain their platform and workload limits.
