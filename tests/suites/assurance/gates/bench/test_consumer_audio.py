"""Audio evidence checks exact outputs, storage, provenance and paired order."""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def profile():
    pytest.importorskip("numpy")
    return importlib.import_module("bench.profiles.consumer_audio")


def test_audio_pair_rejects_same_length_corruption(profile):
    case = profile.Case("corrupt", b"input", lambda: b"right", lambda: b"wrong")
    with pytest.raises(AssertionError, match="candidate output changed"):
        profile._pair(case, 2)


def test_audio_pair_checks_again_after_timing_and_allocation(profile, monkeypatch):
    calls = 0

    def candidate():
        nonlocal calls
        calls += 1
        return b"right" if calls == 1 else b"wrong"

    monkeypatch.setattr(profile, "_calibrate", lambda _function: 1)
    case = profile.Case("drift", b"input", lambda: b"right", candidate)
    with pytest.raises(AssertionError, match="candidate output changed"):
        profile._pair(case, 2)
    assert calls == 5


@pytest.mark.parametrize("storage", ["borrowed", "readonly"])
def test_audio_signature_refuses_unowned_or_readonly_arrays(profile, storage):
    np = pytest.importorskip("numpy")
    value = np.arange(4, dtype=np.float32)
    if storage == "borrowed":
        value = value[:]
    else:
        value.flags.writeable = False
    with pytest.raises(AssertionError, match="own writable storage"):
        profile._signature(value)


def test_audio_cli_writes_checked_timing_memory_and_source_evidence(
    profile, tmp_path, monkeypatch
):
    baseline = tmp_path / "wavx-before.py.txt"
    baseline.write_bytes(profile._CANDIDATE.read_bytes())
    output = tmp_path / "evidence/report.json"

    def cases(before, after):
        raw = profile._pcm(320)
        return [
            profile.Case(
                "wrap/bytes/640",
                raw,
                lambda: before.wrap_pcm(raw, 16000),
                lambda: after.wrap_pcm(raw, 16000),
            ),
            profile.Case(
                "decode/divide/320-frames",
                raw,
                lambda: profile._divide(raw),
                lambda: after.decode_pcm(raw),
            ),
        ]

    monkeypatch.setattr(profile, "_cases", cases)
    monkeypatch.setattr(profile, "_calibrate", lambda _function: 1)
    monkeypatch.setattr(
        profile.sys,
        "argv",
        [
            "consumer_audio",
            "--baseline",
            str(baseline),
            "--output",
            str(output),
            "--rounds",
            "2",
        ],
    )
    profile.main()
    report = json.loads(output.read_text())
    assert report["schema"] == 1
    assert report["source_sha256"][str(baseline)] == profile._sha(baseline.read_bytes())
    assert str(profile._CANDIDATE.parent / "pcm.py") in report["source_sha256"]
    assert report["source_contracts"] == {}
    assert len(report["rows"]) == 2
    for row in report["rows"]:
        assert row["exact_result_checked_before_and_after"]
        assert row["input_bytes"] == 640
        assert row["samples"][0]["order"] == ["baseline", "candidate"]
        assert row["samples"][1]["order"] == ["candidate", "baseline"]
        assert row["median"]["candidate"]["wall_ns"] > 0
        assert row["median"]["candidate"]["thread_cpu_ns"] > 0
        assert row["median"]["candidate"]["input_mib_per_s"] > 0
        assert row["peak_traced_bytes"]["candidate"] > 0


def test_audio_provenance_refuses_missing_consumer_symbols(profile, tmp_path):
    for relative in profile._CONSUMERS:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def unrelated():\n    return None\n")
    with pytest.raises(ValueError, match="missing Consumer audio consumer"):
        profile._consumers(tmp_path)


def test_audio_provenance_retains_the_exact_source_and_symbol_hash(profile, tmp_path):
    for relative, names in profile._CONSUMERS.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n\n".join(f"def {name}():\n    return b'PCM'" for name in names) + "\n"
        )
    contracts = profile._consumers(tmp_path)
    assert len(contracts) == sum(len(names) for names in profile._CONSUMERS.values())
    for key, receipt in contracts.items():
        path, name = key.rsplit(":", 1)
        assert receipt["source"] == f"def {name}():\n    return b'PCM'"
        assert receipt["symbol_sha256"] == profile._sha(receipt["source"].encode())
        assert receipt["file_sha256"] == profile._sha((tmp_path / path).read_bytes())


def test_audio_evidence_rejects_source_edits_during_measurement(
    profile, tmp_path, monkeypatch
):
    baseline = tmp_path / "before.py"
    original = profile._CANDIDATE.read_bytes()
    baseline.write_bytes(original)
    load = profile._load

    def changed(path, name):
        codec = load(path, name)
        if path == baseline:
            path.write_bytes(original + b"\n# changed during measurement\n")
        return codec

    monkeypatch.setattr(profile, "_load", changed)
    monkeypatch.setattr(profile, "_cases", lambda _before, _after: [])
    with pytest.raises(RuntimeError, match="source changed during measurement"):
        profile._report(baseline, None, 2)


def test_decoder_speed_case_is_checked_frozen_and_revision_compatible(monkeypatch):
    from bench import speeds
    from bench.workloads.speed_cases_codecs import _audio_cases
    from unstd.audio import wavx

    name = "unstd.audio.wavx.decode_pcm"
    case = next(case for case in _audio_cases() if case.name == name)
    case.check(case.run())
    row = speeds.load_baseline()["cases"][name]
    assert row["note"] == case.note
    assert 0 < row["measured_ns"] <= row["ceiling_ns"]
    monkeypatch.delattr(wavx, "decode_pcm")
    assert name not in {case.name for case in _audio_cases(comparison=True)}
    with pytest.raises(AttributeError, match="decode_pcm"):
        _audio_cases()
