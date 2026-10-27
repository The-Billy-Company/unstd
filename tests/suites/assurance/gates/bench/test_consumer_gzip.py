"""Workload ports retain budgets, EOF, rewind, flush and byte evidence."""

from __future__ import annotations

from argparse import Namespace
from collections import Counter
import gzip
import io
import json
import re
from types import SimpleNamespace
import zlib

import pytest

from bench.profiles import consumer_gzip as profile
from tests.support.environment.anchors import REPO_ROOT


class Tracked(io.BytesIO):
    def __init__(self, raw, position=0):
        super().__init__(raw)
        super().seek(position)
        self.reads = []
        self.seeks = []

    def read(self, size=-1):
        self.reads.append(size)
        return super().read(size)

    def seek(self, offset, whence=0):
        self.seeks.append((offset, whence))
        return super().seek(offset, whence)


def test_http_port_requests_exactly_the_refusal_sentinel_byte():
    source = Tracked(b"source bytes" * 20)
    codec = SimpleNamespace(GzipFile=lambda **_options: source)
    case = profile.Case("http", "bounded", b"source bytes" * 20, b"", budget=17)
    assert profile._operation(codec, case)() == case.raw[:18]
    assert source.reads == [18]


def test_upload_port_uses_both_256k_passes_and_restores_initial_position():
    pytest.importorskip("blake3")
    raw = bytes(range(256)) * 3000
    source = Tracked(raw, position=17)
    codec = SimpleNamespace(GzipFile=lambda **_options: source)
    case = profile.Case("upload", "upload", raw, b"")
    result = profile._operation(codec, case)()
    assert result[1:] == (len(raw), len(raw), 17)
    assert source.seeks == [(0, 0), (0, 0), (17, 0)]
    assert source.reads == [256 * 1024] * 8


def test_upload_port_restores_position_when_reading_fails():
    pytest.importorskip("blake3")

    class Failed(Tracked):
        def read(self, size=-1):
            message = "source read failed"
            raise OSError(message)

    source = Failed(b"source", position=3)
    codec = SimpleNamespace(GzipFile=lambda **_options: source)
    case = profile.Case("upload", "upload", b"source", b"")
    with pytest.raises(OSError, match="source read failed"):
        profile._operation(codec, case)()
    assert source.seeks[-1] == (3, 0)


def test_query_summary_cannot_hide_same_length_decompression_corruption():
    raw = b'[{"title":"right"}]'
    changed = b'[{"title":"wrong"}]'
    codec = SimpleNamespace(GzipFile=lambda **_options: io.BytesIO(changed))
    case = profile.Case("query", "query", raw, b"")
    with pytest.raises(AssertionError, match="decoded output changed"):
        profile._validate(codec, case, (1, 1))


def test_journal_port_writes_each_line_and_flushes_each_batch():
    calls = []

    class Writer(gzip.GzipFile):
        def write(self, value):
            calls.append(("write", value))
            return super().write(value)

        def flush(self, *args):
            calls.append(("flush", None))
            return super().flush(*args)

    raw = b"one\ntwo\nthree\nfour\nfive\n"
    codec = SimpleNamespace(GzipFile=Writer)
    packed = profile._journal_bytes(codec, raw, batch_rows=2, members=1)
    assert gzip.decompress(packed) == raw
    assert calls == [
        ("write", b"one\n"),
        ("write", b"two\n"),
        ("flush", None),
        ("write", b"three\n"),
        ("write", b"four\n"),
        ("flush", None),
        ("write", b"five\n"),
        ("flush", None),
    ]


def test_journal_reopen_appends_two_real_gzip_members():
    raw = b'{"s":1}\n{"s":2}\n{"s":3}\n{"s":4}\n'
    one = profile._journal_bytes(gzip, raw, batch_rows=2, members=1)
    two = profile._journal_bytes(gzip, raw, batch_rows=2, members=2)
    assert one.count(b"\x1f\x8b\x08") == 1
    assert two.count(b"\x1f\x8b\x08") == 2
    case = profile.Case("journal", "journal-read", raw, two, members=2)
    actual = profile._operation(gzip, case)()
    profile._validate(gzip, case, actual)
    assert actual == [{"s": 1}, {"s": 2}, {"s": 3}, {"s": 4}]


@pytest.mark.parametrize("fault", ["raw-hash", "packed-hash", "different-output"])
@pytest.mark.parametrize("compressed", [False, True])
def test_frozen_fixture_hashes_and_independent_decompression_are_checked(
    tmp_path, fault, compressed
):
    raw = b"source bytes"
    packed = gzip.compress(raw, mtime=0)
    case = profile.Case("bounded", "bounded", raw, packed, budget=3)
    entry = {
        **profile.asdict(case),
        "raw": profile.base64.b64encode(raw).decode(),
        "packed": profile.base64.b64encode(packed).decode(),
        "raw_sha256": profile._sha(raw),
        "packed_sha256": profile._sha(packed),
    }
    if fault == "raw-hash":
        entry["raw_sha256"] = "different"
    elif fault == "packed-hash":
        entry["packed_sha256"] = "different"
    else:
        changed = gzip.compress(b"wrong bytes", mtime=0)
        entry["packed"] = profile.base64.b64encode(changed).decode()
        entry["packed_sha256"] = profile._sha(changed)
    path = tmp_path / ("fixtures.json.gz" if compressed else "fixtures.json")
    payload = json.dumps({"cases": [entry]}).encode()
    path.write_bytes(gzip.compress(payload, mtime=0) if compressed else payload)
    with pytest.raises(ValueError, match=r"fixture (hash|gzip)"):
        profile._load_fixtures(path)


def test_wrong_product_import_is_rejected_before_fixture_loading(tmp_path, monkeypatch):
    monkeypatch.setattr(profile.importlib, "import_module", lambda _name: gzip)
    monkeypatch.setattr(profile.sys, "path", profile.sys.path.copy())
    with pytest.raises(RuntimeError, match="requested gzip source was not imported"):
        profile._worker(
            Namespace(source=tmp_path, stdlib=False, fixtures=None, case=[])
        )


def test_unknown_selection_fails_instead_of_writing_empty_performance_evidence(
    tmp_path, monkeypatch
):
    raw = b"source"
    fake = SimpleNamespace(__file__=str(tmp_path / "unstd/compress/gzipx.py"))
    monkeypatch.setattr(profile.importlib, "import_module", lambda _name: fake)
    monkeypatch.setattr(profile.sys, "path", profile.sys.path.copy())
    monkeypatch.setattr(
        profile,
        "_load_fixtures",
        lambda _path: ([profile.Case("known", "bounded", raw, b"")], {}),
    )
    with pytest.raises(ValueError, match="no Consumer gzip cases selected"):
        profile._worker(
            Namespace(source=tmp_path, stdlib=False, fixtures=None, case=["absent-*"])
        )


def test_production_query_port_exhausts_ijson_and_checks_corrupt_tail():
    pytest.importorskip("ijson")
    raw = json.dumps([{"id": i} for i in range(501)]).encode()
    packed = gzip.compress(raw, mtime=0)
    case = profile.Case("query", "query", raw, packed)
    assert profile._operation(gzip, case)() == (501, 6)
    damaged = bytearray(packed)
    damaged[-8] ^= 1
    damaged_case = profile.Case("query", "query", raw, bytes(damaged))
    with pytest.raises(gzip.BadGzipFile):
        profile._operation(gzip, damaged_case)()


def test_candidate_wall_slowdown_fails_and_retains_cpu_diagnostics(
    tmp_path, monkeypatch
):
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text("{}")

    def worker(command, **_options):
        source = command[command.index("--source") + 1]
        candidate = source.endswith("candidate")
        result = {
            "cases": {
                "upload": {
                    "per_op_ns": 150 if candidate else 100,
                    "cpu_samples_ns": [50 if candidate else 100],
                }
            }
        }
        return SimpleNamespace(stdout=json.dumps(result))

    monkeypatch.setattr(profile.subprocess, "run", worker)
    output = tmp_path / "report.json"
    with pytest.raises(RuntimeError, match="latency regressions"):
        profile._run(
            Namespace(
                source=tmp_path / "candidate",
                baseline_source=tmp_path / "baseline",
                stdlib=False,
                fixtures=fixtures,
                rounds=7,
                repetitions=3,
                case=[],
                output=output,
                max_slowdown=1.3,
            )
        )
    report = json.loads(output.read_text())
    assert report["regressions"] == ["upload"]
    assert report["summary_cpu_ns"]["candidate"]["upload"] == 50
    assert report["summary_wall_ns"]["candidate"]["upload"] == 150


@pytest.mark.parametrize(
    ("ratios", "fails"),
    [
        ((1.20, 1.31, 1.20), False),
        ((1.40, 1.31, 1.40), True),
        ((1.30, 1.40, 1.30), False),
    ],
)
def test_wall_gate_matches_repetitions_under_drift_and_keeps_persistent_slowdowns(
    tmp_path, monkeypatch, ratios, fails
):
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text("{}")
    before_samples = (100.0, 105.0, 200.0)
    observed = []

    def worker(command, **_options):
        repetition = len(observed) // 2
        source = command[command.index("--source") + 1]
        candidate = source.endswith("candidate")
        observed.append("candidate" if candidate else "baseline")
        wall = before_samples[repetition] * (ratios[repetition] if candidate else 1)
        result = {
            "cases": {
                "upload": {
                    "per_op_ns": wall,
                    # Faster CPU time cannot excuse slower wall-clock latency.
                    "cpu_samples_ns": [50 if candidate else 100],
                }
            }
        }
        return SimpleNamespace(stdout=json.dumps(result))

    monkeypatch.setattr(profile.subprocess, "run", worker)
    output = tmp_path / "paired.json"
    arguments = Namespace(
        source=tmp_path / "candidate",
        baseline_source=tmp_path / "baseline",
        stdlib=False,
        fixtures=fixtures,
        rounds=7,
        repetitions=3,
        case=[],
        output=output,
        max_slowdown=1.3,
    )
    if fails:
        with pytest.raises(RuntimeError, match="latency regressions"):
            profile._run(arguments)
    else:
        profile._run(arguments)
    assert observed == [
        "candidate",
        "baseline",
        "baseline",
        "candidate",
        "candidate",
        "baseline",
    ]
    report = json.loads(output.read_text())
    comparison = report["comparisons"]["upload"]
    assert report["max_slowdown"] == 1.3
    assert comparison["paired_candidate_over_baseline"] == pytest.approx(ratios[0])
    assert [pair["candidate_over_baseline"] for pair in comparison["pairs"]] == (
        pytest.approx(ratios)
    )
    assert report["regressions"] == (["upload"] if fails else [])
    # Independent medians select the middle repetition's slowdown even when
    # two matched measurements are within the unchanged limit.
    assert (
        report["summary_wall_ns"]["candidate"]["upload"]
        / report["summary_wall_ns"]["baseline"]["upload"]
        > 1.3
    )
    assert len(report["records"]["candidate"]) == 3
    assert len(report["records"]["baseline"]) == 3


@pytest.mark.parametrize(
    ("maximum", "baseline"),
    [("nan", True), ("inf", True), ("0.99", True), ("-1", True), ("1.3", False)],
)
def test_cli_cannot_disable_the_guard_with_a_nonfinite_or_unpaired_limit(
    maximum, baseline, monkeypatch, capsys
):
    arguments = [
        "consumer_gzip.py",
        "run",
        "--source",
        "candidate",
        "--fixtures",
        "fixture.json.gz",
        "--output",
        "report.json",
        "--max-slowdown",
        maximum,
    ]
    if baseline:
        arguments.extend(("--baseline-source", "baseline"))
    monkeypatch.setattr(profile.sys, "argv", arguments)
    with pytest.raises(SystemExit) as caught:
        profile.main()
    assert caught.value.code == 2
    assert "requires a baseline and a finite ratio" in capsys.readouterr().err


def test_committed_consumer_transport_fixtures_have_all_fourteen_synthetic_shapes():
    path = REPO_ROOT / "bench/workloads/consumer_gzip.json.gz"
    cases, receipt = profile._load_fixtures(path)
    assert receipt["schema"] == 1
    assert set(receipt) == {
        "schema",
        "recorded_utc",
        "source_contracts",
        "method",
        "cases",
    }
    assert len(cases) == len({case.name for case in cases}) == 14
    assert Counter(case.operation for case in cases) == {
        "query": 3,
        "upload": 3,
        "bounded": 2,
        "journal-read": 4,
        "journal-write": 2,
    }
    assert set(receipt["source_contracts"]) == set(profile._CONSUMERS)
    for source in receipt["source_contracts"].values():
        assert set(source) == {
            "path",
            "symbol",
            "line",
            "end_line",
            "file_sha256",
            "symbol_sha256",
        }
        assert source["line"] > 0
        assert source["end_line"] >= source["line"]
        assert re.fullmatch(r"[0-9a-f]{64}", source["file_sha256"])
        assert re.fullmatch(r"[0-9a-f]{64}", source["symbol_sha256"])
    indexed = {case.name: case for case in cases}
    for count in (100, 20001, 40001):
        query = indexed[f"dataset-query-{count}"]
        upload = indexed[f"dataset-upload-{count}"]
        assert query.batch_rows == 100
        assert query.raw == upload.raw
        assert query.packed == upload.packed
        rows = json.loads(query.raw)
        assert len(rows) == count
        for index, row in enumerate(rows):
            assert set(row) == {"id", "title", "value"}
            assert row == {
                "id": index,
                "title": f"café 🎹 source row {index}",
                "value": index / 10,
            }
    success = indexed["http-bounded-success"]
    assert success.budget == len(success.raw)
    assert json.loads(success.raw) == {
        "items": [
            {"id": index, "text": "source response 東京"} for index in range(2000)
        ]
    }
    refusal = indexed["http-expansion-refusal"]
    assert refusal.raw == b" " * 1_000_000
    assert refusal.budget == len(refusal.packed) * 2
    for count in (200, 20000):
        single = indexed[f"journal-replay-{count}-1-members"]
        appended = indexed[f"journal-replay-{count}-2-members"]
        writer = indexed[f"journal-write-flush-{count}"]
        assert single.raw == appended.raw == writer.raw
        assert single.batch_rows == appended.batch_rows == writer.batch_rows == 100
        rows = single.raw.splitlines()
        assert len(rows) == count
        for index, raw in enumerate(rows):
            assert json.loads(raw) == {
                "s": index,
                "w": float(index),
                "m": float(index),
                "k": "tick",
                "p": {"dt": 1.0, "energy": index % 100 / 100, "text": "café 🎹"},
                "h": index,
            }
        for case in (single, appended):
            packed, members = case.packed, 0
            while packed:
                decoder = zlib.decompressobj(31)
                decoder.decompress(packed)
                assert decoder.eof
                packed = decoder.unused_data
                members += 1
            assert members == case.members
