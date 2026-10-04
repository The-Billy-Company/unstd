---
doc_radar:
  sentinels:
    - file: CHANGELOG.md
      contains: ['## [2.1.1]', 'Codec(decimal_format=...)']
    - file: pyproject.toml
      contains: ['exclude-newer = "P2D"']
---

# Unreleased changes

We keep one fragment per change here; release history stays in the root changelog.
Fragments describe what changed for a caller. On release, we fold their bodies
into the root changelog and move their source assertions with them.
