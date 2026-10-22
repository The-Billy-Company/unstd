"""Hashing comparisons load the selected helpers and retain paired evidence."""

from __future__ import annotations

import hashlib
import os
import py_compile
import sys
from types import ModuleType, SimpleNamespace

import pytest

from bench import crypto_hash as compare
import unstd.crypto


@pytest.mark.parametrize(
    "imports",
    [
        "from unstd.crypto._blake3 import VALUE",
        "from ._blake3 import VALUE",
        "import unstd.crypto._blake3\nVALUE = unstd.crypto._blake3.VALUE",
        "import unstd.crypto._blake3 as backend\nVALUE = backend.VALUE",
        "from unstd.crypto import _blake3\nVALUE = _blake3.VALUE",
        "from unstd import crypto\nVALUE = crypto._blake3.VALUE",
    ],
)
def test_saved_helpers_resolve_independently_of_live_crypto(
    tmp_path, monkeypatch, imports
):
    directory = tmp_path / "unstd" / "crypto"
    directory.mkdir(parents=True)
    (directory / "_marker.py").write_text("VALUE = 'saved helper'\n")
    helper = directory / "_blake3.py"
    helper.write_text("from ._marker import VALUE\n")
    (directory / "digest.py").write_text("from unstd.crypto._blake3 import VALUE\n")
    (directory / "token.py").write_text(imports + "\n")
    live = ModuleType("unstd.crypto._blake3")
    live.VALUE = "live helper"
    monkeypatch.setitem(sys.modules, live.__name__, live)
    monkeypatch.setattr(unstd.crypto, "_blake3", live, raising=False)
    original = {
        name: module
        for name, module in sys.modules.items()
        if name == "unstd" or name.startswith("unstd.")
    }

    digest, token = compare._load(tmp_path)

    assert digest.VALUE == token.VALUE == "saved helper"
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "unstd" or name.startswith("unstd.")
    } == original
    assert unstd.crypto._blake3 is live
    namespace = digest.__name__.split(".", 1)[0]
    selected = sys.modules[f"{namespace}.crypto._blake3"]
    assert selected.__file__ == str(helper)
    assert compare._source_hashes(tmp_path)["unstd/crypto/_blake3.py"] == (
        hashlib.sha256(helper.read_bytes()).hexdigest()
    )


def test_absent_saved_helper_fails_without_using_live_helper(tmp_path, monkeypatch):
    directory = tmp_path / "unstd" / "crypto"
    directory.mkdir(parents=True)
    (directory / "digest.py").write_text("from unstd.crypto._blake3 import VALUE\n")
    live = ModuleType("unstd.crypto._blake3")
    live.VALUE = "must never be used"
    monkeypatch.setitem(sys.modules, live.__name__, live)
    original = set(sys.modules)

    with pytest.raises(ModuleNotFoundError, match="missing saved crypto module"):
        compare._load(tmp_path)

    assert not {
        name
        for name in set(sys.modules) - original
        if name.startswith("_crypto_hash_saved_")
    }
    assert sys.modules[live.__name__] is live


def test_saved_source_hash_matches_execution_despite_stale_bytecode(tmp_path):
    directory = tmp_path / "unstd" / "crypto"
    directory.mkdir(parents=True)
    helper = directory / "_blake3.py"
    helper.write_text("VALUE = 'stale'\n")
    original_stat = helper.stat()
    py_compile.compile(str(helper), doraise=True)
    helper.write_text("VALUE = 'fresh'\n")
    os.utime(helper, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    for module in ("digest", "token"):
        (directory / f"{module}.py").write_text("from ._blake3 import VALUE\n")

    digest, token = compare._load(tmp_path)

    assert digest.VALUE == token.VALUE == "fresh"
    assert compare._source_hashes(tmp_path)["unstd/crypto/_blake3.py"] == (
        hashlib.sha256(b"VALUE = 'fresh'\n").hexdigest()
    )


def test_workloads_cover_crossover_sizes_and_each_hashing_mode():
    stub = SimpleNamespace(
        raw=None, hex=None, derive_key=None, mac_raw=None, mac_hex=None, Stream=None
    )
    # Workloads prepare inputs and callables without executing the operations.
    stub.raw = stub.hex = stub.derive_key = stub.mac_raw = stub.mac_hex = lambda: None
    stub.Stream = lambda chunks: SimpleNamespace(hex=lambda width: "hex value")
    cases = compare._workloads((stub, stub), (stub, stub))
    names = {case.name for case in cases}
    for operation in ("raw", "hex", "derive_key", "mac_raw", "mac_hex"):
        assert {
            f"{operation} / {size}"
            for size in (
                "64 B",
                "256 KiB",
                "1 MiB",
                "4 MiB",
                "8 MiB",
                "16 MiB",
                "64 MiB",
            )
        } <= names
    assert len(names) == len(cases)
    assert {"Stream.hex / 64 B", "Stream.hex8 / 64 B"} <= names


@pytest.mark.parametrize("after", [lambda: "value", lambda: b"wrong"])
def test_output_type_or_value_mismatch_aborts_before_timing(monkeypatch, after):
    def unexpected(*args):
        pytest.fail("timing started before outputs matched")

    monkeypatch.setattr(compare, "_calibrate", unexpected)
    case = compare.Workload("wrong", 5, lambda: b"value", after)
    with pytest.raises(AssertionError, match="outputs differ"):
        compare._measure(case, rounds=2, seconds=0.01, case_index=0)


def test_paired_samples_alternate_and_keep_each_observation(monkeypatch):
    before, after = lambda: b"value", lambda: b"value"
    calls = []

    def batch(call, iterations):
        assert iterations == 2
        calls.append("before" if call is before else "after")
        return 400 if call is before else 100

    case = compare.Workload("paired", 5, before, after)
    monkeypatch.setattr(compare, "_calibrate", lambda *args: 2)
    monkeypatch.setattr(compare, "_batch", batch)
    result = compare._measure(case, rounds=3, seconds=0.01, case_index=0)
    assert calls == ["before", "after", "after", "before", "before", "after"]
    assert result["paired_speedups"] == [4, 4, 4]
    assert result["median_paired_speedup"] == 4
    assert [sample["elapsed_ns"] for sample in result["samples"]["before"]] == [400] * 3
    assert [sample["per_op_ns"] for sample in result["samples"]["after"]] == [50] * 3
