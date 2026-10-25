---
doc_radar:
  sentinels:
    - file: wheels.py
      contains: ['def _managed_python(', 'if version("cibuildwheel") != "4.3.1":', 'if not free_threading:', 'def _linux_runtime(', 'replacement.replace(link)']
    - file: ../../../.github/workflows/release.yml
      contains: ['UV_MANAGED_PYTHON: "1"', 'python -m tests.tooling.release.wheels', '--linux-runtime cp${{ matrix.python }}']
    - file: ../../../pyproject.toml
      contains: ['select = "cp3{12,13}-macosx_x86_64"', 'sysconfig.get_config_var("CFLAGS")']
    - file: ../../../hatch_build.py
      contains: ['def _compiler_commands(', 'self.compiler.set_executables(', '"profile_sha256"']
---

# Release verification tooling

[`verify.py`](verify.py) authenticates archive paths, metadata, RECORD entries,
licenses, vendored provenance, and the exact files handed to a publisher without
installing or executing archive contents. [`cache_key.py`](cache_key.py) identifies
reusable build/test dependencies; [`reuse_ci.py`](reuse_ci.py) checks recent
exact-commit CI evidence. [`wheel_tests.py`](wheel_tests.py) runs installed-wheel
contracts in bounded parallel workers.

The verifier requires `unstd.fs._fs_native` in supported Linux native release
wheels and rejects that Linux-only binary in non-Linux wheels. Its `.pyi` typing
stub remains valid on every platform. The build hook
keeps Python usable when an optional local fs compile fails, while strict
release builds fail visibly. Free-threaded configurations retain Python.
Linux cibuildwheel tests import the packaged fs extension before running the
full [fs contracts](../../suites/product/fs/README.md), including the 62 native
integration cases. Their syscall-boundary checks compile a test-only ELF
observer from the source tree when a compiler is available; it is not a
production backend or a profiling dependency. Installed Billy performance
qualification has a separate [driver and retained receipts](../../../bench/reports/fs/production/README.md).

[`wheels.py`](wheels.py) keeps cibuildwheel's build, repair and test pipeline.
Standard native CPython builds on macOS and CPython 3.12 builds on Windows use
uv's managed provider and pass the existing TLS policy before compilation.
Other Windows versions keep their normal provider; a changed builder version or
installer contract requires review.
The Intel 3.12 and 3.13 override preserves the existing 10.13 deployment floor
after retaining the interpreter's defaults or the caller's explicit compiler
flags. Intel 3.14 retains its 10.15 floor.

Linux containers install the locked uv provider, match the original
interpreter's SOABI and native platform, and atomically replace only its
`/opt/python/cp3xx-cp3xx` symlink. Cibuildwheel builds, repairs with auditwheel,
and runs installed-wheel contracts using that admitted interpreter. The
container images and compiler settings stay with cibuildwheel; repaired wheels
must still prove their declared glibc or musl compatibility before publication.
The retained interpreter and locked Setuptools provide the original compiler
commands, including caller overrides. The build hook keeps the managed SDK
paths, rejects old Python SDK paths in those commands, and records the compiler
profile and its SHA256 in the build log. Ordinary builds skip this opt-in input.

```console
python -m tests.tooling.release verify --help
python -m tests.tooling.release.wheels --help
```

The [release gates](../../suites/assurance/gates/release/README.md) test these
instruments. Real source and wheel behavior belongs to the
[packaging plane](../../suites/assurance/planes/packaging/README.md).
