"""Fresh consumers load only the requested container family without API changes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


def test_package_discovery_does_not_load_optional_backends(tmp_path: Path) -> None:
    result = run_python(
        """
import json, sys
import unstd.containers as containers
names = containers.__all__
visible = dir(containers)
try:
    containers.UnknownContainer
except AttributeError as error:
    unknown = str(error)
else:
    raise AssertionError("unknown container unexpectedly resolved")
print(json.dumps({
    "public": names,
    "discoverable": all(name in visible for name in names),
    "cached": any(name in vars(containers) for name in names),
    "loaded": [name for name in ("sortedcontainers", "immutables",
        "unstd.containers.sorted", "unstd.containers.persistent",
        "unstd.containers._sorted_native") if name in sys.modules],
    "unknown": unknown,
}))
""",
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert set(report["public"]) == {"Map", "SortedDict", "SortedList", "SortedSet"}
    assert report["discoverable"]
    assert not report["cached"]
    assert report["loaded"] == []
    assert report["unknown"] == (
        "module 'unstd.containers' has no attribute 'UnknownContainer'"
    )


@pytest.mark.parametrize(
    ("module", "backend", "unused", "flag"),
    [
        ("persistent", "immutables", "sortedcontainers", "HAVE_IMMUTABLES"),
        ("sorted", "sortedcontainers", "immutables", "HAVE_SORTEDCONTAINERS"),
    ],
)
@pytest.mark.parametrize("blocked", [False, True])
def test_direct_submodule_attributes_remain_lazy_and_expose_backend_flags(
    tmp_path: Path, module: str, backend: str, unused: str, flag: str, blocked: bool
) -> None:
    if not blocked:
        require_backend(backend)
    result = run_python(
        f"""
import sys
import unstd.containers as containers
assert {module!r} in dir(containers)
assert {backend!r} not in sys.modules
assert {unused!r} not in sys.modules
if {blocked!r}:
    sys.modules[{backend!r}] = None
resolved = getattr(containers, {module!r})
assert resolved is sys.modules[{f"unstd.containers.{module}"!r}]
assert vars(containers)[{module!r}] is resolved
assert getattr(resolved, {flag!r}) is {not blocked!r}
assert {unused!r} not in sys.modules
""",
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("statement", "name", "wanted", "unused"),
    [
        ("from unstd.containers import Map", "Map", "immutables", "sortedcontainers"),
        (
            "from unstd.containers.persistent import Map",
            "Map",
            "immutables",
            "sortedcontainers",
        ),
        *[
            (
                f"from unstd.containers import {name}",
                name,
                "sortedcontainers",
                "immutables",
            )
            for name in ("SortedDict", "SortedList", "SortedSet")
        ],
        (
            "from unstd.containers.sorted import SortedList",
            "SortedList",
            "sortedcontainers",
            "immutables",
        ),
    ],
)
def test_one_family_import_preserves_backend_type_without_loading_the_other(
    tmp_path: Path, statement: str, name: str, wanted: str, unused: str
) -> None:
    require_backend(wanted)
    result = run_python(
        f"""
import sys
{statement}
assert {wanted!r} in sys.modules
assert {unused!r} not in sys.modules
import unstd.containers as containers
backend = sys.modules[{wanted!r}]
resolved = getattr(containers, {name!r})
assert issubclass(resolved, getattr(backend, {name!r}))
if {wanted!r} == "immutables":
    assert resolved is backend.Map
    assert "unstd.containers._sorted_native" not in sys.modules
assert vars(containers)[{name!r}] is resolved
assert getattr(containers, {name!r}) is resolved
assert {unused!r} not in sys.modules
""",
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_star_import_and_reload_preserve_backend_types(tmp_path: Path) -> None:
    require_backend("sortedcontainers")
    require_backend("immutables")
    result = run_python(
        """
import importlib
from unstd.containers import *
import unstd.containers as containers
import immutables, sortedcontainers
assert Map is immutables.Map
assert issubclass(SortedDict, sortedcontainers.SortedDict)
assert issubclass(SortedList, sortedcontainers.SortedList)
assert issubclass(SortedSet, sortedcontainers.SortedSet)
assert all(name in vars(containers) for name in containers.__all__)
importlib.reload(containers)
assert not any(name in vars(containers) for name in containers.__all__)
assert all(name in dir(containers) for name in containers.__all__)
assert containers.Map is Map
assert containers.SortedDict is SortedDict
assert containers.SortedList is SortedList
assert containers.SortedSet is SortedSet
""",
        cwd=tmp_path,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
