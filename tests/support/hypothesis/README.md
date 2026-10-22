# Shared Hypothesis profiles

`profiles.py` registers generation settings and selects one profile when
`tests/conftest.py` loads. This directory contains configuration, not property
tests. Dedicated properties live under `tests/suites/product/<module>/properties`;
generated cases retained in mixed API files also receive the `properties` marker.

| Profile | Maximum examples | Selection and behavior |
| --- | --- | --- |
| `dev` | 25 | Local default |
| `default` | 100 | Explicit opt-in |
| `ci` | 300 | Default when `CI` is nonempty; deterministic generation |
| `campaign` | 2,500 | Explicit deeper generation |
| `replay` | 1 | Explicit examples and database reuse only; no new generation |

All profiles disable wall-clock deadlines, print reproduction blobs, and retain
Hypothesis health checks. `HYPOTHESIS_PROFILE` overrides the default selection.
From the repository root:

```console
uv run --no-sync pytest -q -m properties
HYPOTHESIS_PROFILE=ci uv run --no-sync pytest -q -m properties
HYPOTHESIS_PROFILE=campaign uv run --no-sync pytest -q -m properties
```

Keep generation depth here so campaign runs are not capped inside individual
tests. `replay` is a reproduction aid rather than a generative CI gate. Preserve
confirmed failures as explicit examples or named regressions; the local example
database is only a cache.
