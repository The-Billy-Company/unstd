---
doc_radar:
  sentinels:
    - file: pyproject.toml
      contains: ['"msgspec>=0.22"', 'exclude-newer = "P2D"']
    - file: src/unstd/serde/structs.py
      contains: ['Callable[[Decimal], object]', 'decimal_format: _DecimalFormat']
    - file: .github/workflows/ci.yml
      contains: ['# v10.2.0', 'version: "latest-known"', 'uv sync --locked']
---

We refreshed the optional-backend and development lock, including uuid-utils
1.0 and msgspec 0.22. Existing UUID generation and codec defaults keep their
behavior; only the serde extra's msgspec floor moves to 0.22.

`Codec(decimal_format=...)` now accepts a callable over each `Decimal`, so
exact decimal policies stay exact instead of passing through a float.
Callback errors propagate, and the same encoder remains usable afterward.
[msgspec 0.22](https://github.com/msgspec/msgspec/releases/tag/0.22.0) added
the underlying encoder support.

We quarantine new resolver and isolated-build releases for two days. CI uses
the reviewed lock and checksum-backed uv versions from the pinned
[setup-uv 10.2 action](https://github.com/astral-sh/setup-uv/releases/tag/v10.2.0).
The separate floor job still proves the oldest supported direct dependencies.
