"""The real import guards with each backend blocked in a fresh interpreter.

``sys.modules[name] = None`` makes ``import name`` raise ``ImportError`` exactly
as an absent distribution does, so the guarded modules take their placeholder
branch even where the ``containers`` extra is installed. The other backend stays
importable: one missing dependency must not disable the other family.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


_PROBE = """
import json, sys
sys.modules[sys.argv[1]] = None
import unstd.containers as containers
from unstd.containers import persistent, sorted as sorted_

report = {"flags": [sorted_.HAVE_SORTEDCONTAINERS, persistent.HAVE_IMMUTABLES], "names": {}}
for name in containers.__all__:
    try:
        built = getattr(containers, name)()
    except ImportError as error:
        report["names"][name] = ["ImportError", str(error)]
    else:
        report["names"][name] = ["built", type(built).__module__]
print(json.dumps(report))
"""

_MESSAGE = (
    "unstd.containers.{name} requires the 'containers' extra"
    " — install with: pip install 'unstd[containers]'"
)


@pytest.mark.parametrize(
    ("blocked", "guarded", "flags"),
    [
        ("sortedcontainers", {"SortedDict", "SortedList", "SortedSet"}, [False, True]),
        ("immutables", {"Map"}, [True, False]),
    ],
)
def test_one_blocked_backend_guards_only_its_own_names(
    tmp_path: Path, blocked: str, guarded: set[str], flags: list[bool]
) -> None:
    require_backend(
        "immutables" if blocked == "sortedcontainers" else "sortedcontainers"
    )
    result = run_python(_PROBE, [blocked], cwd=tmp_path, timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["flags"] == flags
    for name, (outcome, detail) in report["names"].items():
        if name in guarded:
            assert (outcome, detail) == ("ImportError", _MESSAGE.format(name=name))
        else:
            assert outcome == "built"
            assert detail.split(".")[0] in {"sortedcontainers", "immutables"}
            assert detail.split(".")[0] != blocked
    assert set(report["names"]) == {"Map", "SortedDict", "SortedList", "SortedSet"}


def test_present_backends_are_re_exported_verbatim() -> None:
    sortedcontainers = require_backend("sortedcontainers")
    immutables = require_backend("immutables")
    import unstd.containers as containers

    assert containers.SortedDict is sortedcontainers.SortedDict
    assert containers.SortedList is sortedcontainers.SortedList
    assert containers.SortedSet is sortedcontainers.SortedSet
    assert containers.Map is immutables.Map
