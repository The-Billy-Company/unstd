"""Hypothesis profiles, and the rule that lets the suite run on a base install.

``ci`` is what GitHub Actions loads. ``dev`` is the local default. ``campaign``
is the deep sweep.
"""

from __future__ import annotations

import importlib
import importlib.util
import os

from hypothesis import HealthCheck, settings


settings.register_profile(
    "dev",
    max_examples=20,
    deadline=None,
    suppress_health_check=(HealthCheck.too_slow,),
)


settings.register_profile(
    "ci",
    max_examples=80,
    deadline=None,
    suppress_health_check=(HealthCheck.too_slow,),
)
settings.register_profile(
    "campaign",
    max_examples=1000,
    deadline=None,
    suppress_health_check=(HealthCheck.too_slow,),
)

_default = "ci" if os.environ.get("CI") else "dev"
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", _default))


# --------------------------------------------------------------------------
# Running on a base install
#
# Most of `unstd` degrades to the stdlib when its extra is absent, and those
# tests run everywhere — exercising the fallback is the entire point of
# installing without extras. Four surfaces have no faithful stand-in and say so
# by raising at import (`crypto` has only BLAKE2 to fall back to, which is a
# different algorithm; pydantic, msgspec and whenever have no stdlib analogue at
# all). Their test modules therefore cannot be imported, and pytest reports an
# uncollectable module as an *error*, so a bare `pip install unstd && pytest`
# aborted the whole run before this rule — which meant the fallback paths, the
# library's central promise, had never been exercised by anything.
#
# Each entry names the `unstd` module rather than the backend behind it. The
# module is what has to import, its own guarded `ImportError` already carries the
# "install the X extra" hint, and naming it survives a backend being swapped
# underneath (`rex` traded RE2 for irgx mid-flight; a hardcoded "re2" here would
# have quietly started skipping a suite that was fine).
_NEEDS: dict[str, str] = {
    "crypto/*.py": "unstd.crypto",
    "model/*.py": "unstd.model",
    "serde/test_structs.py": "unstd.serde.structs",
    "time/test_dateutil.py": "unstd.time.dateutil",
    # Not an extra: the benchmark harness lives outside `src/` and is absent from
    # an installed wheel, so its own tests only mean something in a checkout.
    "test_bench.py": "bench",
}


def _importable(module: str) -> bool:
    try:
        importlib.import_module(module)
    except ImportError:
        return False
    return True


collect_ignore_glob = [
    pattern for pattern, module in _NEEDS.items() if not _importable(module)
]
