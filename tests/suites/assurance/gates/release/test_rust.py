"""Pinned compiler bootstrapping authenticates bytes before execution."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.tooling.release import rust


TARGET = "aarch64-unknown-linux-musl"
PAYLOAD = b"reviewed bootstrap executable fixture"


class Response(io.BytesIO):
    def __init__(self, payload=PAYLOAD, url="https://static.rust-lang.org/archive"):
        super().__init__(payload)
        self.url = url

    def geturl(self):
        return self.url


@pytest.mark.parametrize("identifier", ["cp312", "cp313", "cp314"])
def test_older_python_does_not_probe_download_or_execute(identifier, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("older Python must not provision Rust")

    monkeypatch.setattr(rust.subprocess, "check_output", unexpected)
    monkeypatch.setattr(rust.subprocess, "run", unexpected)
    monkeypatch.setattr(rust, "urlopen", unexpected)
    assert rust.provision(identifier)["identifier"] == identifier


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
@pytest.mark.parametrize("libc", ["gnu", "musl"])
def test_target_uses_selected_native_python_abi(machine, libc, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(rust.sys, "platform", "linux")
    monkeypatch.setattr(rust.platform, "machine", lambda: machine)

    def probe(command, **kwargs):
        calls.append((command, kwargs))
        return f"cpython-315-{machine}-linux-{libc}\n"

    monkeypatch.setattr(rust.subprocess, "check_output", probe)
    assert rust.native_target("cp315", tmp_path) == f"{machine}-unknown-linux-{libc}"
    assert calls[0][0][:4] == [tmp_path / "cp315-cp315/bin/python", "-I", "-S", "-c"]
    assert calls[0][1]["timeout"] == 30


@pytest.mark.parametrize(
    "identifier", ["cp315t", "pp315", "cp3", "cp٣١٥", "cp315/other"]
)
def test_invalid_identifier_is_rejected_before_probing(identifier):
    with pytest.raises(ValueError, match="standard CPython"):
        rust.native_target(identifier)


@pytest.mark.parametrize(
    "soabi",
    [
        "cpython-315-x86_64-linux-musl",
        "cpython-314-aarch64-linux-musl",
        "cpython-315t-aarch64-linux-musl",
        "cpython-315-aarch64-linux-unknown",
        "pypy-315-aarch64-linux-musl",
    ],
)
def test_foreign_or_unaudited_abi_is_rejected(soabi, monkeypatch):
    monkeypatch.setattr(rust.sys, "platform", "linux")
    monkeypatch.setattr(rust.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(rust.subprocess, "check_output", lambda *args, **kwargs: soabi)
    with pytest.raises(RuntimeError, match="unreviewed native Python ABI"):
        rust.native_target("cp315")


@pytest.mark.parametrize(
    ("system", "machine"), [("darwin", "aarch64"), ("linux", "i686")]
)
def test_non_native_platform_is_rejected(system, machine, monkeypatch):
    monkeypatch.setattr(rust.sys, "platform", system)
    monkeypatch.setattr(rust.platform, "machine", lambda: machine)
    with pytest.raises(RuntimeError, match="native Linux"):
        rust.native_target("cp315")


@pytest.fixture
def authenticated_fixture(monkeypatch):
    monkeypatch.setitem(rust.RUSTUP_SHA256, TARGET, hashlib.sha256(PAYLOAD).hexdigest())
    monkeypatch.setattr(rust, "native_target", lambda identifier: TARGET)


def test_download_uses_pinned_archive_and_installs_authenticated_bytes(
    authenticated_fixture, monkeypatch, tmp_path
):
    calls = []

    def download(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(rust, "urlopen", download)
    installer = rust.authenticated_installer(TARGET, tmp_path)
    assert calls == [
        (
            f"https://static.rust-lang.org/rustup/archive/{rust.RUSTUP_VERSION}/{TARGET}/rustup-init",
            {"timeout": 60},
        )
    ]
    assert installer.read_bytes() == PAYLOAD
    assert installer.stat().st_mode & 0o111 == 0o111
    assert list(installer.parent.iterdir()) == [installer]


@pytest.mark.parametrize("failure", ["hash", "http-redirect", "size"])
def test_rejected_download_never_reaches_execution(
    authenticated_fixture, monkeypatch, tmp_path, failure
):
    payload = b"tampered" if failure == "hash" else PAYLOAD
    url = (
        "http://static.rust-lang.org/archive"
        if failure == "http-redirect"
        else "https://static.rust-lang.org/archive"
    )
    monkeypatch.setattr(rust, "urlopen", lambda *args, **kwargs: Response(payload, url))
    if failure == "size":
        monkeypatch.setattr(rust, "_DOWNLOAD_LIMIT", len(PAYLOAD) - 1)
    calls = []
    monkeypatch.setattr(
        rust.subprocess, "run", lambda *args, **kwargs: calls.append(args)
    )
    with pytest.raises(RuntimeError, match=r"HTTPS|checksum or size mismatch"):
        rust.provision("cp315", tmp_path)
    assert not calls
    assert not list((tmp_path / "downloads").iterdir())


@pytest.mark.parametrize("tampered", [False, True])
def test_cached_bootstrap_is_reauthenticated_without_network_fallback(
    authenticated_fixture, monkeypatch, tmp_path, tampered
):
    directory = tmp_path / "downloads"
    directory.mkdir()
    installer = directory / f"rustup-init-{rust.RUSTUP_VERSION}-{TARGET}"
    installer.write_bytes(b"tampered" if tampered else PAYLOAD)

    def unexpected(*args, **kwargs):
        pytest.fail("cached executable must be verified locally")

    monkeypatch.setattr(rust, "urlopen", unexpected)
    if tampered:
        with pytest.raises(RuntimeError, match="checksum or size mismatch"):
            rust.authenticated_installer(TARGET, tmp_path)
    else:
        assert rust.authenticated_installer(TARGET, tmp_path) == installer


@pytest.mark.parametrize("wrong_tool", [None, "rustup", "rustc", "cargo"])
def test_provision_revalidates_pinned_tools_even_with_global_cargo(
    monkeypatch, tmp_path, wrong_tool
):
    monkeypatch.setattr(rust, "native_target", lambda identifier: TARGET)
    installer = tmp_path / "authenticated-rustup-init"
    monkeypatch.setattr(rust, "authenticated_installer", lambda *args: installer)
    monkeypatch.setenv("PATH", "/unrelated/global/cargo:/bin")
    monkeypatch.setenv("RUSTUP_TOOLCHAIN", "nightly")
    monkeypatch.setenv("RUSTUP_DIST_SERVER", "http://untrusted.invalid")
    calls = []
    monkeypatch.setattr(
        rust.subprocess, "run", lambda args, **kwargs: calls.append((args, kwargs))
    )

    def version(args, **kwargs):
        tool = args[0].name
        expected = {
            "rustup": rust.RUSTUP_VERSION,
            "rustc": rust.RUST_VERSION,
            "cargo": rust.CARGO_VERSION,
        }[tool]
        return f"{tool} {'0.0.0' if tool == wrong_tool else expected} (reviewed commit)"

    monkeypatch.setattr(rust.subprocess, "check_output", version)
    if wrong_tool is not None:
        with pytest.raises(RuntimeError, match=f"provisioned {wrong_tool}.*pinned"):
            rust.provision("cp315", tmp_path)
    else:
        assert rust.provision("cp315", tmp_path)["target"] == TARGET
    command, options = calls[0]
    assert command == [
        installer,
        "-y",
        "--no-modify-path",
        "--profile",
        "minimal",
        "--default-host",
        TARGET,
        "--default-toolchain",
        rust.RUST_VERSION,
    ]
    assert options["check"] is True
    assert options["timeout"] == 300
    environment = options["env"]
    assert environment["PATH"].split(os.pathsep)[0] == str(tmp_path / "cargo/bin")
    assert environment["CARGO_HOME"] == str(tmp_path / "cargo")
    assert environment["RUSTUP_HOME"] == str(tmp_path / "rustup")
    assert environment["RUSTUP_DIST_SERVER"] == "https://static.rust-lang.org"
    assert environment["RUSTUP_UPDATE_ROOT"] == "https://static.rust-lang.org/rustup"
    assert "RUSTUP_TOOLCHAIN" not in environment
    assert os.environ["RUSTUP_TOOLCHAIN"] == "nightly"


def test_installer_failure_propagates_before_version_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(rust, "native_target", lambda identifier: TARGET)
    monkeypatch.setattr(
        rust, "authenticated_installer", lambda *args: tmp_path / "installer"
    )
    failure = subprocess.CalledProcessError(1, "rustup-init")

    def reject(*args, **kwargs):
        raise failure

    monkeypatch.setattr(rust.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        rust.provision("cp315", tmp_path)
    assert caught.value is failure


def test_relative_root_is_rejected_before_download(monkeypatch):
    monkeypatch.setattr(rust, "native_target", lambda identifier: TARGET)
    with pytest.raises(ValueError, match="absolute installation root"):
        rust.provision("cp315", Path("relative"))


def test_command_reports_provisioning_evidence(monkeypatch, capsys):
    expected = {"identifier": "cp315", "target": TARGET}
    monkeypatch.setattr(rust, "provision", lambda *args: expected)
    assert rust.main(["cp315"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
