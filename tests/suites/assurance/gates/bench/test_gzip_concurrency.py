"""Concurrent gzip evidence counts complete work and keeps source isolation."""

from __future__ import annotations

from argparse import ArgumentTypeError, Namespace
import gc
import gzip
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from bench.profiles import gzip_concurrency as profile


def test_upload_throughput_divisor_counts_workers_iterations_and_both_passes():
    payload = profile._payload_bytes("upload", 100, 0)
    result = profile._rates(1_000_000_000, 2_000_000_000, 4, 3, payload)
    assert result["operations"] == 12
    assert result["logical_bytes"] == 2400
    assert result["logical_bytes_per_second"] == 2400
    assert result["operations_per_second"] == 12
    assert result["process_cpu_ns"] == 2_000_000_000
    assert profile._payload_bytes("bounded", 1000, 17) == 18
    assert profile._payload_bytes("query", 1000, 17) == 1000


@pytest.mark.parametrize("counts", ["0", "5", "1,1", "1,no", ""])
def test_worker_budget_is_bounded_and_duplicate_free(counts):
    with pytest.raises(ArgumentTypeError):
        profile._workers(counts)


def test_checksums_cannot_stand_in_for_actual_native_decoding():
    with pytest.raises(RuntimeError, match="native gzip codec"):
        profile._require_native({"unstd.compress._gzip_native.crc32": 1}, "upload")
    with pytest.raises(RuntimeError, match="native gzip codec"):
        profile._require_native({"unstd.compress._gzip_native.decompress": 0}, "upload")
    profile._require_native({"unstd.compress._gzip_native.decompress": 1}, "upload")
    profile._require_native({"zlib_ng.zlib_ng.read": 1}, "query")
    profile._require_native({"zlib_ng.zlib_ng.compress": 1}, "journal-write")


def test_wrong_output_fails_before_timing():
    calls = []

    def invalid():
        calls.append("operation")
        return b"changed"

    def check(value):
        assert value == b"original", "decoded bytes changed"

    with pytest.raises(AssertionError, match="decoded bytes changed"):
        profile._measure(invalid, check, workers=2, rounds=1, payload_bytes=8)
    assert calls == ["operation"]


def test_failed_timed_operation_restores_collector(monkeypatch):
    enabled = gc.isenabled()
    state = {"active": False}

    def operation():
        if state["active"]:
            message = "timed operation failed"
            raise OSError(message)
        return b"source"

    def batch(*_args):
        state["active"] = True
        return profile._repeat(operation, 1)

    monkeypatch.setattr(profile, "_batch", batch)
    with pytest.raises(OSError, match="timed operation failed"):
        profile._measure(
            operation, lambda _value: None, workers=2, rounds=1, payload_bytes=6
        )
    assert gc.isenabled() is enabled


def test_wrong_installation_is_rejected_before_fixture_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(profile.importlib, "import_module", lambda _name: gzip)
    monkeypatch.setattr(profile.sys, "path", profile.sys.path.copy())
    with pytest.raises(RuntimeError, match="requested gzip source was not imported"):
        profile._worker(Namespace(source=tmp_path, fixtures=None))


def test_direct_help_entrypoint_needs_no_product_or_optional_backend(tmp_path):
    result = subprocess.run(  # noqa: S603 — fixed local script and interpreter, without site hooks
        [sys.executable, "-S", str(Path(profile.__file__).resolve()), "run", "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--baseline-source" in result.stdout
    assert "--workers" in result.stdout


def test_sources_and_worker_counts_alternate_without_entering_the_timer(
    tmp_path, monkeypatch
):
    commands = []
    fixtures = tmp_path / "fixtures.json.gz"
    fixtures.write_bytes(b"frozen fixture bytes")

    def worker(command, **_options):
        commands.append(command)
        workers = int(command[command.index("--workers") + 1])
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "case": "upload",
                    "raw_sha256": "same raw",
                    "packed_sha256": "same compressed",
                    "source_contracts": {"source": "same caller"},
                    "measurement": {
                        "workers": workers,
                        "samples": [{"logical_bytes_per_second": workers * 100}],
                    },
                }
            )
        )

    monkeypatch.setattr(profile.subprocess, "run", worker)
    output = tmp_path / "report.json"
    profile._run(
        Namespace(
            source=tmp_path / "candidate",
            baseline_source=tmp_path / "baseline",
            fixtures=fixtures,
            case="upload",
            workers=(1, 2),
            rounds=2,
            output=output,
        )
    )
    order = [
        (
            Path(command[command.index("--source") + 1]).name,
            command[command.index("--workers") + 1],
        )
        for command in commands
    ]
    assert order == [
        ("candidate", "1"),
        ("baseline", "1"),
        ("candidate", "2"),
        ("baseline", "2"),
        ("baseline", "2"),
        ("candidate", "2"),
        ("baseline", "1"),
        ("candidate", "1"),
    ]
    for command in commands:
        assert command[1] == str(Path(profile.__file__).resolve())
        assert command[2] == "worker"
        assert command[command.index("--rounds") + 1] == "1"
    report = json.loads(output.read_text())
    assert report["candidate_speedup"] == {"1": 1.0, "2": 1.0}
    assert report["median_logical_bytes_per_second"]["candidate"] == {
        "1": 100,
        "2": 200,
    }


def test_different_compressed_fixtures_cannot_support_a_comparison(
    tmp_path, monkeypatch
):
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_bytes(b"manifest")

    def worker(command, **_options):
        source = command[command.index("--source") + 1]
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "case": "upload",
                    "raw_sha256": "same raw",
                    "packed_sha256": source,
                    "source_contracts": {},
                    "measurement": {
                        "workers": 1,
                        "samples": [{"logical_bytes_per_second": 100}],
                    },
                }
            )
        )

    monkeypatch.setattr(profile.subprocess, "run", worker)
    with pytest.raises(RuntimeError, match="different packed_sha256"):
        profile._run(
            Namespace(
                source=tmp_path / "candidate",
                baseline_source=tmp_path / "baseline",
                fixtures=fixtures,
                case="upload",
                workers=(1,),
                rounds=1,
                output=tmp_path / "report.json",
            )
        )
