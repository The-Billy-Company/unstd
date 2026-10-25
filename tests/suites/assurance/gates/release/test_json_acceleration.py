"""Installed JSON qualification must reject correct but unaccelerated paths."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import tomllib
from types import SimpleNamespace

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.runtime import json_acceleration as runtime


_SCRIPT = REPO_ROOT / "tests/tooling/runtime/json_acceleration.py"


def test_every_release_wheel_checks_its_installed_json_accelerations():
    with (REPO_ROOT / "pyproject.toml").open("rb") as stream:
        commands = tomllib.load(stream)["tool"]["cibuildwheel"]["test-command"]
    assert (
        "python {project}/tests/tooling/runtime/json_acceleration.py --json" in commands
    )


def _run(tmp_path, setup=""):
    pytest.importorskip("unstd.serde._json_native")
    source = (
        textwrap.dedent(f"""\
        import importlib.util
        spec = importlib.util.spec_from_file_location('qualification', {str(_SCRIPT)!r})
        qualification = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(qualification)
    """)
        + textwrap.dedent(setup)
        + "\nraise SystemExit(qualification.main(['--json']))\n"
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local qualification script
        [sys.executable, "-c", source],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert not result.stderr, result.stderr
    return result.returncode, json.loads(result.stdout)


def test_standalone_check_accepts_actual_native_paths_outside_the_checkout(tmp_path):
    code, report = _run(tmp_path)
    assert code == 0, report
    assert report["errors"] == []
    assert set(report["checks"]) == {
        "bindings",
        "encoding",
        "repeated_floats",
        "ascii",
        "decoding",
        "strict_identity",
        "ndjson",
    }
    assert len(report["native_sha256"]) == 64
    assert report["executable"] == sys.executable


@pytest.mark.parametrize("missing", ["orjson", "msgspec", "unstd.serde._json_native"])
def test_missing_backend_or_extension_fails_with_actionable_evidence(
    monkeypatch, capsys, missing
):
    def unavailable(name):
        if name == missing:
            message = f"required installed module absent: {name}"
            raise ModuleNotFoundError(message)
        return SimpleNamespace()

    monkeypatch.setattr(runtime, "import_module", unavailable)
    assert runtime.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["checks"] == []
    assert len(report["errors"]) == 1
    assert missing in report["errors"][0]


@pytest.mark.parametrize(
    ("method", "check"),
    [
        ("encode_text", "repeated_floats"),
        ("ascii_text", "ascii"),
        ("loads_lines", "decoding"),
        ("identity_snapshot", "strict_identity"),
    ],
)
def test_native_refusal_cannot_pass_through_correct_public_fallbacks(
    tmp_path, method, check
):
    code, report = _run(
        tmp_path,
        f"""
        from unstd.serde import jsonx, _json_native as native
        native.{method} = lambda *args: NotImplemented
        assert jsonx.dumpb([1, 2]) == b'[1,2]'
    """,
    )
    assert code == 1, report
    assert check not in report["checks"]
    assert any(error.startswith(check + ":") for error in report["errors"])


def test_equal_but_shared_identity_snapshot_is_rejected(tmp_path):
    code, report = _run(
        tmp_path,
        """
        from unstd.serde import _json_native as native
        native.identity_snapshot = lambda source: source
    """,
    )
    assert code == 1
    assert any("private branch ownership" in error for error in report["errors"])


def test_missing_public_binding_is_rejected_even_when_extension_still_encodes(tmp_path):
    code, report = _run(
        tmp_path,
        """
        from unstd.serde import jsonx, _json_native as native
        jsonx._native_dumpb = None
        assert native.encode([1, 2]) == b'[1,2]'
        assert jsonx.dumpb([1, 2]) == b'[1,2]'
    """,
    )
    assert code == 1
    assert any(
        "_native_dumpb lost its native binding" in error for error in report["errors"]
    )


@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason="3.14 function dispatch")
def test_inactive_function_dispatch_cannot_pass_correct_output(tmp_path):
    code, report = _run(
        tmp_path,
        """
        from unstd.serde import jsonx, _json_native as native
        native.dispatch_ready = lambda function: False
        assert jsonx.loads('[1,2]') == [1, 2]
    """,
    )
    assert code == 1
    assert any(
        "native function dispatch is inactive" in error for error in report["errors"]
    )
