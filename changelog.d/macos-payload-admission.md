---
doc_radar:
  sentinels:
    - file: tests/tooling/release/verify.py
      contains: ['def _macos_floors(', 'def _macos_payload(', 'Mach-O deployment version']
---

We verify the architecture and deployment floor inside each macOS wheel's Mach-O payloads, including embedded binaries. A newer minimum OS, malformed load-command table or missing deployment command fails release admission even when the archive's hashes and metadata are valid. The standalone verifier keeps its standard-library-only trust boundary.
