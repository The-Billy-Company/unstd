---
doc_radar:
  sentinels:
    - file: .github/workflows/ci.yml
      contains: ['github.sha || github.ref']
    - file: .github/workflows/release.yml
      contains: ['TAG: ${{ github.ref_name }}', 'uv version --short']
---

We keep each main commit's CI run in its own concurrency group, so a newer push
cannot replace its pending checks. A release tag must also agree with the
package's declared version before the wheel is built for publication.
