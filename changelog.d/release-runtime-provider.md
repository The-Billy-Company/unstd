---
doc_radar:
  sentinels:
    - file: .github/workflows/release.yml
      contains: ['UV_MANAGED_PYTHON: "1"', 'python -m tests.tooling.release.wheels']
    - file: tests/tooling/release/wheels.py
      contains: ['def _validate_runtime(', 'if version("cibuildwheel") != "4.3.1":']
    - file: pyproject.toml
      contains: ['select = "cp312-macosx_x86_64"', 'sysconfig.get_config_var("CFLAGS")']
---

We use admitted managed interpreters for release hosts and native CPython 3.12 wheel builds on macOS and Windows. The pinned builder still owns compilation, repair and tests; runtime ABI and TLS checks run before compilation. Intel builds request the existing macOS 10.13 floor while preserving the selected interpreter's optimization flags. The tooling CI cohort installs its declared build and release dependencies, so the compiler checks run on the supported dependency floors too.
