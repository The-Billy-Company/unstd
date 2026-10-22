"""Real type and lint gates reject independently seeded annotation regressions."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]


_VALID_TYPING = """from collections.abc import Awaitable, Callable, Iterable
from typing import Literal, Self
from typing_extensions import override

class Base:
    value: object = 0

    def read(self) -> object:
        return self.value

class Child(Base):
    @override
    def read(self) -> object:
        return self.value

    def identity(self) -> Self:
        return self

def has_values(values: Iterable[int]) -> bool:
    return bool(tuple(values))

async def resolve(value: Awaitable[int]) -> int:
    return await value

def initialized(flag: bool) -> int:
    value = 0
    if flag:
        value = 1
    return value

def exhaustive(value: Literal['one', 'two']) -> None:
    match value:
        case 'one':
            pass
        case 'two':
            pass

def bytes_only(value: bytes) -> bytes:
    return value

bytes_only(bytes(bytearray(b'value')))

def preserve(function: Callable[[int], int]) -> Callable[[int], int]:
    return function

@preserve
def decorated(value: int) -> int:
    return value
"""

_INVALID_TYPING = """import asyncio
from collections.abc import Callable, Iterable
from typing import Any, Literal, Self
from typing_extensions import deprecated
from missing_typing_regression_dependency import ExternalType

def has_values(values: Iterable[int]) -> bool:
    if values:
        return True
    return False

async def operation() -> None:
    pass

def launch() -> None:
    asyncio.create_task(operation())

class Base:
    value: object = 0

    def read(self) -> object:
        return self.value

class Child(Base):
    value: int = 0

    def read(self) -> object:
        return self.value

    def identity(self: Self) -> Self:
        return self

def inspect(value: int) -> None:
    reveal_type(value)

def maybe_initialized(flag: bool) -> int:
    if flag:
        value = 1
    return value

def incomplete(value: Literal['one', 'two']) -> None:
    match value:
        case 'one':
            pass

@deprecated('Use the replacement function')
def obsolete() -> None:
    pass

obsolete()

def imported_any(value: ExternalType) -> None:
    pass

def none_comparison(value: int) -> bool:
    return value is None

def bytes_only(value: bytes) -> None:
    pass

bytes_only(bytearray(b'value'))

ignored: int = 'value'  # type: ignore

def erase_return(function: Callable[[int], int]) -> Callable[[int], Any]:
    return function

@erase_return
def decorated(value: int) -> int:
    return value
"""

_VALID_LINT = '''"""An annotated identity operation."""


def identity(value: int) -> int:
    """Return the input value."""
    return value
'''

_INVALID_LINT = '''"""Annotation and suppression regressions."""


def identity(value):
    """Return the input value."""
    return value


ignored: int = "value"  # type: ignore
suppressed: int = "value"  # noqa
'''


def _mypy(source: Path) -> subprocess.CompletedProcess[str]:
    pytest.importorskip("mypy")
    return subprocess.run(  # noqa: S603 — analyze fixture source; never execute it
        [
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(REPO_ROOT / "pyproject.toml"),
            "--no-incremental",
            # Standalone fixtures do not import the production override modules.
            "--no-warn-unused-configs",
            "--no-error-summary",
            "--show-error-codes",
            "--no-pretty",
            str(source),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _ruff(source: str, filename: str) -> subprocess.CompletedProcess[str]:
    pytest.importorskip("ruff")
    return subprocess.run(  # noqa: S603 — analyze fixture source; never execute it
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(REPO_ROOT / "pyproject.toml"),
            "--output-format=json",
            "--stdin-filename",
            str(REPO_ROOT / "src" / filename),
            "-",
        ],
        cwd=REPO_ROOT,
        input=source,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_real_mypy_accepts_annotated_control_using_repository_policy(tmp_path) -> None:
    source = tmp_path / "valid_typing.py"
    source.write_text(_VALID_TYPING, encoding="utf-8")
    result = _mypy(source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == result.stderr == ""


def test_real_mypy_rejects_strict_typing_regressions_using_repository_policy(
    tmp_path,
) -> None:
    source = tmp_path / "invalid_typing.py"
    source.write_text(_INVALID_TYPING, encoding="utf-8")
    result = _mypy(source)
    assert result.returncode == 1, result.stdout + result.stderr
    codes = {
        line.rsplit("[", 1)[-1].removesuffix("]")
        for line in result.stdout.splitlines()
        if ": error:" in line
    }
    assert {
        "truthy-iterable",
        "unused-awaitable",
        "explicit-override",
        "mutable-override",
        "redundant-self",
        "unimported-reveal",
        "possibly-undefined",
        "exhaustive-match",
        "deprecated",
        "import-not-found",
        "no-any-unimported",
        "comparison-overlap",
        "arg-type",
        "ignore-without-code",
    } <= codes, result.stdout
    assert 'Type of decorated function contains type "Any"' in result.stdout
    assert result.stderr == ""


def test_real_ruff_accepts_annotated_control_using_repository_policy() -> None:
    result = _ruff(_VALID_LINT, "valid_typing.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == []
    assert result.stderr == ""


def test_real_ruff_requires_annotations_and_specific_suppression_codes() -> None:
    result = _ruff(_INVALID_LINT, "invalid_typing.py")
    assert result.returncode == 1, result.stdout + result.stderr
    assert {"ANN001", "ANN201", "PGH003", "PGH004"} <= {
        finding["code"] for finding in json.loads(result.stdout)
    }, result.stdout
    assert result.stderr == ""


def test_real_ruff_checks_stub_annotations_using_repository_policy() -> None:
    source = '"""A quoted annotation regression."""\n\ndef identity() -> "int": ...\n'
    result = _ruff(source, "invalid_typing.pyi")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PYI020" in {finding["code"] for finding in json.loads(result.stdout)}
    assert result.stderr == ""
