"""Request timings retain checkpoint byte identity and refuse invalid cases."""

from argparse import Namespace
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
import zlib

import pytest

from bench.profiles import consumer_gzip_requests as profile


def test_direct_gzip_api_preserves_request_wire_and_rejects_checkpoint_use():
    codec = profile._GzipCodec(gzip)
    body = profile._body(4096)
    case = profile.Case("direct", "direct", ((body, 6),))
    result = profile._operation(codec, case)()
    profile._validate(codec, case, result)
    assert result == (gzip.compress(body, compresslevel=6, mtime=0),)
    assert codec.compress(body, wbits=31) == result[0]
    with pytest.raises(ValueError, match="gzip window"):
        codec.compress(body)
    with pytest.raises(NotImplementedError, match="direct encodes"):
        codec.compressobj(level=6, wbits=31)


@pytest.mark.parametrize("size", [128 * 1024, 512 * 1024])
@pytest.mark.parametrize("level", [1, 6, 9])
@pytest.mark.parametrize("mode", ["scope", "warm"])
def test_checkpoint_port_preserves_exact_stdlib_compressed_bytes(size, level, mode):
    requests = tuple((profile._body(size, revision), level) for revision in range(3))
    case = profile.Case("request", mode, requests)
    operation = profile._operation(zlib, case)
    profile._validate(zlib, case, operation())
    profile._validate(zlib, case, operation())


def test_checkpoint_retains_only_prefix_and_releases_retired_state():
    owner = profile._Prefix(zlib)
    body = profile._body(128 * 1024)
    assert owner.encode(body, 6) is None
    assert owner._state[1] == body[:65536]
    assert owner._state[2:] == (b"", None)
    assert owner.encode(body, 6) == zlib.compress(body, level=6, wbits=31)
    state = owner._state
    assert owner.encode(body, 1) is None
    assert owner.encode(profile._body(128 * 1024, changed_prefix=True), 6) is None
    assert owner._state is state
    owner.close()
    assert owner._state is None
    assert owner.encode(body, 6) is None


def test_request_skips_small_and_nonshrinking_bodies():
    called = []

    def compress(data, *, level, wbits):
        called.append((level, wbits))
        return data + b"larger"

    codec = SimpleNamespace(compress=compress)
    small = b"x" * 1023
    large = bytes(range(256)) * 8
    assert profile._request(codec, small, 6, None) is small
    assert called == []
    assert profile._request(codec, large, 9, None) is large
    assert called == [(9, 31)]


def test_same_length_wrong_checkpoint_bytes_are_rejected():
    case = profile.Case("body", "direct", ((b"right" * 1024, 6),))
    correct = profile._operation(zlib, case)()
    changed = bytes([correct[0][0] ^ 1]) + correct[0][1:]
    with pytest.raises(AssertionError, match="checkpoint compressed bytes changed"):
        profile._validate(zlib, case, (changed,))


def test_frozen_request_fixture_checks_original_bytes_and_source_receipts():
    fixture = (
        Path(__file__).resolve().parents[5]
        / "bench/workloads/consumer_gzip_requests.json.gz"
    )
    cases, receipts = profile._load(fixture)
    assert len(cases) == 11
    assert set(receipts) == {
        "gzip_encode",
        "gzip_request",
        "_GzipPrefix",
        "dataset_materialize_encode",
    }
    for case in cases:
        for body, _ in case.requests:
            if case.mode == "materialize":
                assert isinstance(json.loads(body), list)
            else:
                assert json.loads(body)["model"] == "benchmark-model"
    first = cases[3].requests
    assert first[0][0][:65536] == first[-1][0][:65536]
    assert first[0][0] != first[-1][0]


def test_materialization_preserves_canonical_rows_inside_base64_gzip():
    body = b'[{"id":1,"text":"hello"},{"id":2,"text":"world"}]'
    case = profile.Case("materialize", "materialize", ((body, 9),))
    result = profile._operation(zlib, case)()
    assert isinstance(result[0], str)
    profile._validate(zlib, case, result)
    with pytest.raises(AssertionError, match="materialized content changed"):
        profile._validate(zlib, case, (result[0][:-1] + "!",))


def test_contract_failure_is_retained_while_other_cases_are_timed(
    monkeypatch, tmp_path
):
    children = []

    class Child:
        def __init__(self, command, **_options):
            self.commands = []
            self.stdin = SimpleNamespace(
                write=self.write, flush=lambda: None, close=lambda: None
            )
            self.stdout = SimpleNamespace(readline=self.readline)
            self.started = False
            self.current = "current" in command[command.index("--source") + 1]
            children.append(self)

        def write(self, line):
            self.commands.append(json.loads(line))

        def readline(self):
            if not self.started:
                self.started = True
                return json.dumps(
                    {
                        "cases": [
                            {
                                "name": "valid",
                                "raw_sha256": ["same"],
                                "levels": [6],
                                "raw_bytes": 7,
                                "eligible": True,
                                "contract_failure": None,
                            },
                            {
                                "name": "invalid",
                                "raw_sha256": ["same"],
                                "levels": [6],
                                "raw_bytes": 7,
                                "eligible": not self.current,
                                "contract_failure": "changed bytes"
                                if self.current
                                else None,
                            },
                        ]
                    }
                )
            return json.dumps(
                {"cpu_ns": 10000000, "wall_ns": 10000000, "iterations": 1}
            )

        def wait(self, **_options):
            return 0

    monkeypatch.setattr(profile.subprocess, "Popen", Child)
    fixture = tmp_path / "fixtures"
    fixture.write_bytes(b"fixture identity")
    output = tmp_path / "result.json"
    args = Namespace(
        source=tmp_path / "current",
        baseline_source=tmp_path / "before",
        fixtures=fixture,
        require_native=False,
        case=[],
        rounds=2,
        batch_ms=1,
        max_slowdown=1.3,
        output=output,
    )
    with pytest.raises(RuntimeError, match="Consumer request contract failures"):
        profile._run(args)
    report = json.loads(output.read_text())
    assert report["cases"][0]["speedup_cpu"] == 1
    assert report["cases"][1]["eligible"] is False
    assert report["cases"][1]["contract_failures"] == {"candidate": "changed bytes"}
    assert {item["case"] for child in children for item in child.commands} == {0}
