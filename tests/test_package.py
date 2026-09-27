"""Drift gates on the package's own description of itself.

The top-level docstring and the README are the two places a reader learns what
``unstd`` contains, and both are hand-written prose about a directory listing —
which is exactly the shape of claim that rots silently. Twelve groups shipped
while the docstring still named four, so nothing here is hypothetical.

Each test derives its expectation from disk or from ``pyproject.toml`` and holds
the prose to it, so adding a group or an extra without describing it fails.
"""

from __future__ import annotations

import importlib
from pathlib import Path
import re
import tomllib

import pytest

import unstd


# The checkout, found from this file rather than from `unstd.__file__`. The
# package resolves to `site-packages/unstd` when the wheel is what got installed,
# and `parents[1]` of that is the interpreter's lib directory — which holds no
# pyproject.toml and no README. The test file, by contrast, is only ever read out
# of the checkout that owns the prose it is gating.
_REPO = Path(__file__).parents[1]
_ROOT = Path(unstd.__file__).parent
_DOC = unstd.__doc__ or ""


def _pyproject() -> dict[str, object]:
    with (_REPO / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _groups() -> list[str]:
    """Every importable subpackage of ``unstd``, read off disk."""
    return sorted(
        p.name
        for p in _ROOT.iterdir()
        if p.is_dir()
        and not p.name.startswith(("_", "."))
        and (p / "__init__.py").is_file()
    )


def _extras() -> list[str]:
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    return sorted(project.get("optional-dependencies", {}))


def _readme() -> str:
    return (_REPO / "README.md").read_text(encoding="utf-8")


def test_disk_has_the_groups_the_readme_advertises() -> None:
    """The set on disk and the set in the README table are the same set."""
    advertised = set(re.findall(r"\[`(\w+)`\]\(src/unstd/", _readme()))
    assert advertised == set(_groups())


@pytest.mark.parametrize("group", _groups())
def test_group_is_named_in_the_top_level_docstring(group: str) -> None:
    """A group nobody can find is a group that does not ship."""
    assert f"``{group}``" in _DOC


@pytest.mark.parametrize("group", _groups())
def test_group_has_a_readme(group: str) -> None:
    assert (_ROOT / group / "README.md").is_file()


@pytest.mark.parametrize("group", _groups())
def test_group_has_tests(group: str) -> None:
    tests = Path(__file__).parent / group
    assert tests.is_dir(), f"unstd.{group} ships with no test directory"
    assert list(tests.glob("test_*.py")), f"tests/{group}/ holds no test module"


@pytest.mark.parametrize("extra", _extras())
def test_extra_is_documented(extra: str) -> None:
    """An undocumented extra is a feature only its author knows how to enable."""
    assert extra in _readme(), (
        f"extra `{extra}` is declared but never mentioned in the README"
    )
    assert f"``{extra}``" in _DOC, (
        f"extra `{extra}` is absent from the package docstring"
    )


def test_the_base_install_has_no_third_party_closure() -> None:
    """``pip install unstd`` must cost the caller nothing but ``unstd``.

    This is the package's headline claim and the reason a lightweight consumer
    can depend on it at all — every backend rides an extra, so no one inherits
    orjson or pydantic for the sake of one helper. The way it breaks is
    mundane: somebody needs a library in one module, adds it to
    ``[project].dependencies`` because that is the field that works, and the
    claim is false from that commit on with nothing to notice. So the required
    set is asserted empty, which is the whole invariant, statable in one line.
    """
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    assert project.get("dependencies", []) == [], (
        "unstd advertises a zero-dependency base install; move this to an extra"
    )


def test_no_extra_pins_an_exact_version() -> None:
    """A library that pins a patch resolves for its consumers, and breaks them.

    Every extra here shipped with ``==`` in 1.0.2, inherited from the monorepo
    this package was extracted from — where an exact pin is right, because a
    monorepo *is* the application. Published, the same pin means an application
    already on ``pydantic==2.13.4`` cannot install ``unstd[model]`` at all, and
    the resolver blames the application. Two such libraries in one environment
    are simply not co-installable.

    So the invariant is the shape of the specifier, not any particular version:
    floors only. A consumer wanting an exact version still has its own manifest
    and lockfile to say so in.
    """
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    extras = project.get("optional-dependencies", {})
    assert isinstance(extras, dict)
    pinned = {
        f"{extra}: {req}"
        for extra, reqs in extras.items()
        for req in reqs
        if "==" in req
    }
    assert not pinned, f"extras must declare floors, not exact pins: {sorted(pinned)}"


def test_the_typing_marker_ships() -> None:
    """Without ``py.typed`` every annotation in this library is invisible.

    The whole surface is annotated and CI runs ``mypy --strict`` over it, but
    PEP 561 says a consumer must ignore all of that unless the marker is present
    in the installed package — so a downstream ``jsonx.dumps`` reads as ``Any``,
    and a strict downstream then writes a suppression per call site to get its
    build green. That is a silent failure on our side that costs someone else
    real type safety, and nothing in the source tree notices it.
    """
    assert (_ROOT / "py.typed").is_file()


def test_version_matches_pyproject() -> None:
    """``__version__`` is read from install metadata, so this catches a stale build.

    It used to be a literal beside the manifest's, and the two drifted on the
    release that renamed nothing and bumped a patch — the tag was cut, the gate
    failed on its own package's version, and no artifact shipped. Deriving it
    removes the second place to edit; what is left to assert is that the
    environment under test was actually built from *this* manifest.
    """
    project = _pyproject()["project"]
    assert isinstance(project, dict)
    assert unstd.__version__ == project["version"]


@pytest.mark.parametrize("group", _groups())
def test_group_either_imports_or_names_the_extra_it_needs(group: str) -> None:
    """A base install must never produce a bare ``ModuleNotFoundError``.

    Two outcomes are correct and there is no third. Most groups **import**, having
    degraded to the stdlib — that is the guarded-fallback contract, and it is why
    ``import unstd.rex`` works with no regex backend present, deferring the
    question to the call that actually needs one. Four surfaces have no faithful
    stand-in and so **refuse**, on purpose: ``crypto.digest`` would have to fork the
    digest to BLAKE2, and pydantic, msgspec and whenever have no stdlib analogue
    at all.

    What is asserted here is that the refusal is a *good* one — an ``ImportError``
    naming the extra that fixes it. The failure mode this guards against is a
    guard being dropped or a new backend imported bare, which turns a two-second
    fix into a stack trace pointing at a third-party module the reader has never
    heard of.

    This test only means something on an install that is missing extras; with
    everything present, every branch is the trivial one. The bare leg in CI is
    what gives it teeth.
    """
    try:
        importlib.import_module(f"unstd.{group}")
    except ImportError as exc:
        refusal = str(exc)
    else:
        return  # Imported. The fallback held, and there is nothing else to ask.

    assert "extra" in refusal, (
        f"unstd.{group} refuses to import but does not say which extra fixes it "
        f"— a caller reading this gets no way forward: {refusal}"
    )
