"""Process evidence checks exact output, provenance and native acceptance."""

from __future__ import annotations

import gc
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
_SPEC = importlib.util.spec_from_file_location(
    "_billy_proc_gate_profile", REPO_ROOT / "bench/profiles/billy_proc.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
profile = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = profile
_SPEC.loader.exec_module(profile)


def _result(case, **changes):
    fields = {
        "argv": case.argv,
        "returncode": 0,
        "stdout": case.stdout,
        "stderr": case.stderr,
    }
    return SimpleNamespace(**(fields | changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"returncode": 7},
        {"argv": ("other",)},
        {"stdout": "missing NUL"},
        {"stdout": b"exact\x00text\n"},
        {"stderr": "unexpected"},
    ],
)
def test_incorrect_output_is_rejected_before_any_timed_batch(changes):
    case = profile.Workload("fixture", ("child",), "exact\x00text\n")
    calls = []

    def run(*args, **kwargs):
        calls.append(True)
        return _result(case, **changes)

    with pytest.raises(RuntimeError, match="independent Billy fixture"):
        profile._measure(run, case, None)
    assert len(calls) == 1


@pytest.mark.parametrize("response", [123, NotImplemented])
def test_profile_distinguishes_accepted_native_calls_from_declined_calls(response):
    case = profile.Workload("fixture", ("child",))
    native_calls = []

    def spawn():
        native_calls.append(True)
        return response

    native = SimpleNamespace(spawn=spawn)

    def run(*args, **kwargs):
        native.spawn()
        return _result(case)

    measured = profile._measure(run, case, native)
    route = measured["profile"]["native_routes"]["spawn"]
    assert route == {
        "calls": 1,
        "accepted": int(response is not NotImplemented),
        "declined": int(response is NotImplemented),
        "raised": 0,
    }
    assert native.spawn is spawn
    # Native instrumentation must be absent from all timed repetitions.
    assert len(native_calls) == case.iterations + 1


def test_profile_restores_native_functions_after_original_error():
    failure = RuntimeError("fixture native failure")

    def spawn():
        raise failure

    native = SimpleNamespace(spawn=spawn)
    case = profile.Workload("fixture", ("child",))

    def run(*args, **kwargs):
        native.spawn()

    with pytest.raises(RuntimeError) as caught:
        profile._profile(run, case, native)
    assert caught.value is failure
    assert native.spawn is spawn


def test_timed_failure_restores_gc():
    enabled = gc.isenabled()
    case = profile.Workload("fixture", ("child",))
    calls = 0

    def run(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            message = "timed launch failure"
            raise OSError(message)
        return _result(case)

    with pytest.raises(OSError, match="timed launch"):
        profile._measure(run, case, None)
    assert gc.isenabled() is enabled


def test_text_oracle_preserves_nul_and_replaces_partial_multibyte_sequences():
    assert profile._decoded(b"a\x00\xc3\r\nline\rend\n\xc2") == "a\x00�\nline\nend\n�"


def test_fixture_environment_cannot_redirect_git_to_external_repository(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GIT_DIR", "/foreign/repo/.git")
    monkeypatch.setenv("GIT_INDEX_FILE", "/foreign/index")
    monkeypatch.setenv("PKG_CONFIG_SYSROOT_DIR", "/foreign/sysroot")
    environment = profile._environment(tmp_path)
    assert "GIT_DIR" not in environment
    assert "GIT_INDEX_FILE" not in environment
    assert "PKG_CONFIG_SYSROOT_DIR" not in environment
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["PKG_CONFIG_LIBDIR"] == str(tmp_path / "pkgconfig")


def test_fingerprints_include_stub_and_native_binary_changes(tmp_path):
    package = tmp_path / "unstd/proc"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("# fixture\n")
    (package / "_proc_native.pyi").write_text("def spawn() -> int: ...\n")
    binary = package / "_proc_native.so"
    binary.write_bytes(b"first binary")
    first = profile._source_hashes(tmp_path)
    binary.write_bytes(b"different binary")
    second = profile._source_hashes(tmp_path)
    assert "unstd/proc/_proc_native.pyi" in first
    assert first["unstd/proc/_proc_native.so"] != second["unstd/proc/_proc_native.so"]


def test_unknown_source_receipt_cannot_masquerade_as_current_billy_call():
    case = profile.Workload("fixture", ("child",), receipts=("invented-callsite",))
    with pytest.raises(RuntimeError, match="unknown Billy source receipts"):
        profile._validate_receipts([case])


def test_direct_help_needs_no_product_or_optional_dependencies(tmp_path):
    result = subprocess.run(  # noqa: S603 — fixed local script without site imports
        [sys.executable, "-S", str(Path(profile.__file__).resolve()), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--require-native" in result.stdout
    assert "--baseline-source" in result.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="real disposable Git fixture")
def test_fixture_metadata_and_inventory_have_independent_exact_oracles(tmp_path):
    fixture = profile._prepare_fixture(tmp_path / "fixture")
    cases, _skipped = profile._workloads(fixture)
    profile._validate_receipts(cases)
    selected = {
        "git-revision-cwd",
        "git-revision-no-cwd",
        "git-inventory-nul",
        "git-status-clean",
        "git-status-dirty",
        "git-diff-numstat",
        "git-show-1mib",
    }
    for case in cases:
        if case.name in selected:
            options = case.options | {
                "env": profile._environment(Path(fixture["root"]))
            }
            result = profile._stdlib_call(case.api, case.argv, **options)
            profile._check(result, case)
    manifest = json.loads((Path(fixture["root"]) / "fixture.json").read_text())
    assert manifest["tracked_source_files"] == 129
    assert len(set(manifest["inventory"].rstrip("\0").split("\0"))) == 130


@pytest.mark.parametrize(
    ("mode", "enabled", "disabled"),
    [("spawn", "spawn", "communicate"), ("io", "communicate", "spawn")],
)
def test_stage_control_declines_other_kernel_before_it_runs(mode, enabled, disabled):
    calls = []

    def spawn(*args, **kwargs):
        calls.append("spawn")
        return 123

    def communicate(*args, **kwargs):
        calls.append("communicate")
        return b"stdout", b"stderr"

    native = SimpleNamespace(spawn=spawn, communicate=communicate)
    backend = SimpleNamespace(_native=native)
    assert profile._configure_mode(backend, mode) is native
    assert backend._SPAWN_NATIVE is (mode == "spawn")
    assert getattr(native, disabled)("arbitrary arguments") is NotImplemented
    assert calls == []
    assert getattr(native, enabled)() is not NotImplemented
    assert calls == [enabled]


@pytest.mark.parametrize(
    ("mode", "enabled", "disabled"),
    [("spawn", "spawn", "communicate"), ("io", "communicate", "spawn")],
)
@pytest.mark.parametrize(
    "failure", ["declined-enabled", "accepted-disabled", "missing-decline"]
)
def test_stage_proof_rejects_inactive_or_leaking_kernels(
    mode, enabled, disabled, failure
):
    routes = {
        enabled: {"accepted": 1, "declined": 0},
        disabled: {"accepted": 0, "declined": int(mode == "spawn")},
    }
    if failure == "declined-enabled":
        routes[enabled] = {"accepted": 0, "declined": 1}
    elif failure == "accepted-disabled":
        routes[disabled] = {"accepted": 1, "declined": 0}
    else:
        routes[disabled] = {"accepted": 0, "declined": int(mode == "io")}
    with pytest.raises(RuntimeError):
        profile._verify_routes(mode, routes, require_native=True, case="fixture")


@pytest.mark.parametrize(
    ("mode", "enabled", "disabled"),
    [("spawn", "spawn", "communicate"), ("io", "communicate", "spawn")],
)
def test_stage_proof_accepts_only_successful_selected_kernel(mode, enabled, disabled):
    profile._verify_routes(
        mode,
        {
            enabled: {"accepted": 1, "declined": 0},
            disabled: {"accepted": 0, "declined": int(mode == "spawn")},
        },
        require_native=True,
        case="fixture",
    )


@pytest.mark.parametrize(
    ("spawn_enabled", "stdin_text", "expected"),
    [
        (False, False, ("communicate",)),
        (True, False, ("spawn", "communicate")),
        (False, True, ()),
        (True, True, ("spawn",)),
    ],
)
def test_candidate_native_proof_follows_platform_and_stdin_admission(
    spawn_enabled, stdin_text, expected
):
    assert (
        profile._expected_native(
            "candidate", spawn_enabled=spawn_enabled, stdin_text=stdin_text
        )
        == expected
    )
    routes = {kernel: {"accepted": 1} for kernel in expected}
    profile._verify_routes(
        "candidate",
        routes,
        require_native=True,
        case="fixture",
        spawn_enabled=spawn_enabled,
        stdin_text=stdin_text,
    )
    if expected:
        del routes[expected[-1]]
        with pytest.raises(RuntimeError, match="did not successfully use"):
            profile._verify_routes(
                "candidate",
                routes,
                require_native=True,
                case="fixture",
                spawn_enabled=spawn_enabled,
                stdin_text=stdin_text,
            )


@pytest.mark.parametrize("accepted", [0, 1])
def test_linux_candidate_cannot_enter_native_spawn_even_if_it_declines(accepted):
    with pytest.raises(RuntimeError, match="native spawn"):
        profile._verify_routes(
            "candidate",
            {
                "spawn": {"accepted": accepted, "declined": 1 - accepted},
                "communicate": {"accepted": 1},
            },
            require_native=True,
            case="fixture",
            spawn_enabled=False,
        )
