---
doc_radar:
  sentinels:
    - file: wheels.py
      contains: ['def _managed_python(', 'if version("cibuildwheel") != "4.3.1":', 'if version == "3.12" and not free_threading:']
    - file: ../../../.github/workflows/release.yml
      contains: ['UV_MANAGED_PYTHON: "1"', 'python -m tests.tooling.release.wheels']
    - file: ../../../pyproject.toml
      contains: ['select = "cp312-macosx_x86_64"', 'sysconfig.get_config_var("CFLAGS")']
---

# Release verification tooling

[`verify.py`](verify.py) authenticates archive paths, metadata, RECORD entries,
licenses, vendored provenance, and the exact files handed to a publisher without
installing or executing archive contents. [`cache_key.py`](cache_key.py) identifies
reusable build/test dependencies; [`reuse_ci.py`](reuse_ci.py) checks recent
exact-commit CI evidence. [`wheel_tests.py`](wheel_tests.py) runs installed-wheel
contracts in bounded parallel workers.

[`wheels.py`](wheels.py) keeps cibuildwheel's build, repair and test pipeline.
Native CPython 3.12 builds on macOS and Windows use uv's managed provider and
pass the existing TLS policy before compilation. Other versions and platforms
keep their normal provider; a changed builder version or installer contract
requires review.
The Intel 3.12 override appends the deployment target after preserving the
interpreter's defaults or the caller's explicit compiler flags.

```console
python -m tests.tooling.release verify --help
python -m tests.tooling.release.wheels --help
```

The [release gates](../../suites/assurance/gates/release/README.md) test these
instruments. Real source and wheel behavior belongs to the
[packaging plane](../../suites/assurance/planes/packaging/README.md).
