"""Native zlib workloads require independent bytes, budgets and copied states."""

from __future__ import annotations

import inspect
from pathlib import Path
import zlib

import pytest

from bench import speeds
from bench.workloads import speed_cases_codecs as workloads
from unstd.compress import zlibx


def test_zlib_workloads_cover_declared_callables_and_pass_repeated_audits():
    cases = workloads._zlib_cases()
    speeds.validate_cases(cases)
    names = {
        name
        for name in speeds._exports(Path(zlibx.__file__))
        if callable(value := getattr(zlibx, name))
        and not (inspect.isclass(value) and issubclass(value, BaseException))
    }
    assert {case.name.rsplit(".", 1)[1].split("#", 1)[0] for case in cases} == names
    speeds.audit(cases)
    speeds.audit(cases)


def test_only_comparison_mode_accepts_an_absent_zlibx_module(monkeypatch):
    def missing(_name):
        message = "older product has no zlibx"
        raise ModuleNotFoundError(message, name="unstd.compress.zlibx")

    monkeypatch.setattr(workloads, "import_module", missing)
    assert workloads._zlib_cases(comparison=True) == []
    with pytest.raises(ModuleNotFoundError, match="older product"):
        workloads._zlib_cases()


@pytest.mark.parametrize("comparison", [False, True])
def test_a_missing_backend_or_internal_dependency_is_not_an_absent_api(
    monkeypatch, comparison
):
    def broken(_name):
        message = "backend import failed"
        raise ModuleNotFoundError(message, name="native_dependency")

    monkeypatch.setattr(workloads, "import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="backend import failed"):
        workloads._zlib_cases(comparison=comparison)


def test_comparison_skips_apis_missing_from_a_partial_older_surface(monkeypatch):
    monkeypatch.delattr(zlibx, "compressobj")
    cases = workloads._zlib_cases(comparison=True)
    assert not any(".compressobj" in case.name for case in cases)
    speeds.audit(cases)
    with pytest.raises(speeds.RegistryError, match="compressobj"):
        speeds.audit(workloads._zlib_cases())


def test_compression_oracle_does_not_reuse_the_product_decoder(monkeypatch):
    case = next(
        case for case in workloads._zlib_cases() if case.name.endswith(".compress")
    )
    encoded = case.run()
    raw = zlib.decompress(encoded)
    calls = []

    def dishonest(*args, **kwargs):
        calls.append((args, kwargs))
        return raw

    monkeypatch.setattr(zlibx, "decompress", dishonest)
    with pytest.raises(zlib.error):
        case.check(b"invalid compressed output")
    assert calls == []


def test_compressor_copy_oracle_rejects_a_reused_branch():
    case = next(
        case
        for case in workloads._zlib_cases()
        if case.name.endswith(".compressobj#copy")
    )
    left, _right = case.run()
    with pytest.raises(speeds.RegistryError):
        case.check((left, left))


def test_dictionary_oracle_rejects_a_codec_that_ignored_the_dictionary():
    cases = {case.name.rsplit(".", 1)[1]: case for case in workloads._zlib_cases()}
    ordinary = cases["compressobj"].run()
    with pytest.raises(AssertionError, match="dictionary flag"):
        cases["compressobj#dictionary"].check(ordinary)


def test_bounded_output_oracle_rejects_an_ignored_limit():
    case = next(
        case
        for case in workloads._zlib_cases()
        if case.name.endswith(".decompressobj#bounded-tail")
    )
    raw, eof, unused, _largest = case.run()
    with pytest.raises(speeds.RegistryError):
        case.check((raw, eof, unused, len(raw)))
