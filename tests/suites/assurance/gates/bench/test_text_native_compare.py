"""Native comparison evidence rejects wrong outputs and false acceleration."""

from __future__ import annotations

import builtins
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.profiles import text_native_compare as compare
from tests.support.environment.anchors import REPO_ROOT


@pytest.fixture
def bundle():
    native = pytest.importorskip("unstd.text._text_native")
    return compare._bundle(
        REPO_ROOT / "src" / "unstd" / "text" / native.__file__.rsplit("/", 1)[-1],
        REPO_ROOT / "src",
        "gate_compare",
    )


def test_scalar_parity_requires_type_and_float_bits() -> None:
    assert compare._same(3, 3)
    assert compare._same(0.5, 0.5)
    assert not compare._same(True, 1)
    assert not compare._same(1, 1.0)
    assert not compare._same(-0.0, 0.0)
    assert not compare._same(0.5, 0.5000000000000001)


def test_explicit_native_path_rejects_absent_and_non_native_files(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="missing native extension"):
        compare._load_native(tmp_path / "missing.so", "gate_missing")
    source = tmp_path / "_text_native.py"
    source.write_text("distance = lambda a, b: 0\n")
    with pytest.raises(ValueError, match="not a native extension path"):
        compare._load_native(source, "gate_fake")


def test_native_output_mismatch_cannot_be_hidden_by_correct_wrapper(bundle) -> None:
    bundle["native"] = SimpleNamespace(distance=lambda a, b: 999)
    case = compare.Case("distance/wrong", "distance", "kitten", "sitting")
    with pytest.raises(AssertionError, match="incorrect native output"):
        compare._validate(bundle, [case])


def test_refused_native_call_cannot_be_presented_as_acceleration(bundle) -> None:
    bundle["native"] = SimpleNamespace(distance=lambda a, b: NotImplemented)
    case = compare.Case("distance/refused", "distance", "kitten", "sitting")
    with pytest.raises(AssertionError, match="native kernel refused"):
        compare._validate(bundle, [case])


def test_portable_wrapper_result_cannot_hide_native_route_refusal(bundle) -> None:
    bundle["wrapper"]._native_distance = lambda a, b: NotImplemented
    original = bundle["wrapper"]._native_distance
    case = compare.Case("distance/hidden-fallback", "distance", "kitten", "sitting")
    with pytest.raises(AssertionError, match="unexpected native/fallback route"):
        compare._validate(bundle, [case])
    assert bundle["wrapper"]._native_distance is original


def test_wrapper_output_mismatch_aborts_before_timing(bundle) -> None:
    bundle["wrapper"]._jaro_winkler = lambda a, b: 0.0
    case = compare.Case("jaro/wrong", "jaro", "MARTHA", "MARHTA")
    with pytest.raises(AssertionError, match="incorrect wrapper output"):
        compare._validate(bundle, [case])


def test_real_cases_cover_unicode_boundaries_large_inputs_and_fallbacks(bundle) -> None:
    cases = compare._cases()
    names = {case.name for case in cases}
    assert {
        f"{kernel}/{alphabet}{length}"
        for kernel in ("distance", "jaro")
        for alphabet in ("ascii", "bmp", "surrogate", "emoji")
        for length in (12, 64, 65, 256, 1024)
    } <= names
    checked = compare._validate(bundle, cases)
    assert checked["distance/sequence-fallback"]["native_returns"] == []
    assert checked["jaro/subclass-fallback"]["route"] == "portable fallback"
    assert checked["distance/large-budget"]["native_returns"] == [True]
    assert checked["jaro/large-budget"]["native_returns"] == [True]


def test_separate_wrapper_copies_keep_their_own_native_bindings(bundle) -> None:
    other = compare._bundle(
        Path(bundle["native"].__file__), REPO_ROOT / "src", "gate_independent"
    )
    case = compare.Case("distance/independent", "distance", "kitten", "sitting")
    bundle["wrapper"]._native_distance = lambda a, b: 999
    assert compare._call(bundle, case)() == 999
    assert compare._call(other, case)() == 3
    assert (
        other["wrapper"]._levenshtein.__globals__
        is not bundle["wrapper"]._levenshtein.__globals__
    )


def test_paired_timings_alternate_order_and_retain_all_batch_samples(
    monkeypatch,
) -> None:
    calls = []
    bundles = {
        label: {
            "wrapper": SimpleNamespace(_levenshtein=lambda a, b, value=label: value),
            "native": SimpleNamespace(distance=lambda a, b, value=label: value),
        }
        for label in ("baseline", "candidate")
    }
    monkeypatch.setattr(compare, "_calibrate", lambda fn: (2, 1))

    def batch(fn, iterations):
        assert iterations == 2
        label = fn()
        calls.append(label)
        return 200 if label == "baseline" else 100

    monkeypatch.setattr(compare, "_batch", batch)
    result = compare._timings(
        bundles,
        [compare.Case("distance/pair", "distance", "a", "b")],
        rounds=2,
        repetitions=2,
    )
    expected_order = ["baseline", "candidate", "candidate", "baseline"] * 2
    assert calls == expected_order * 2
    for kind in ("wrapper", "native"):
        timing = result[f"{kind}/distance/pair"]
        assert timing["batch_ns"] == {
            "baseline": [200] * 4,
            "candidate": [100] * 4,
        }
        assert timing["median_ns"] == {"baseline": 100, "candidate": 50}
        assert timing["baseline_over_candidate"] == 2
        assert timing["paired_ratios"] == [2] * 4


def test_wrapper_imports_only_selected_extension_and_base_dependencies(
    bundle, monkeypatch
) -> None:
    original_import = builtins.__import__
    observed = []

    def reject_implicit_native_or_optional(name, *args, **kwargs):
        observed.append(name)
        if (
            name == "unstd.text._text_native"
            or name == "rapidfuzz"
            or name.startswith("rapidfuzz.")
        ):
            pytest.fail(f"implicit native or optional backend import leaked: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_implicit_native_or_optional)
    selected = compare._bundle(
        Path(bundle["native"].__file__), REPO_ROOT / "src", "gate_import_isolation"
    )
    assert "difflib" in observed
    assert selected["wrapper"]._native_distance is selected["native"].distance
    assert selected["wrapper"]._native_jaro is selected["native"].jaro
    assert selected["wrapper"]._HAVE_RAPIDFUZZ is False
    assert selected["wrapper"]._rf is None


def test_memory_oracle_allocations_happen_after_saved_peak(monkeypatch) -> None:
    events = []
    case = compare.Case("distance/large-budget", "distance", "a", "b", large=True)
    monkeypatch.setattr(compare, "_bundle", lambda *args: {})
    monkeypatch.setattr(compare, "_cases", lambda: [case])

    def call():
        events.append("call")
        return 1

    def rss():
        events.append("rss")
        return len(events) * 1024

    def oracle(selected_case):
        assert selected_case is case
        events.append("oracle")
        return 1

    monkeypatch.setattr(compare, "_call", lambda *args: call)
    monkeypatch.setattr(compare, "_rss_bytes", rss)
    monkeypatch.setattr(compare, "_expected", oracle)
    result = compare._memory_worker(Path("unused"), Path("unused"), case.name)
    assert events == ["rss", *(["call"] * 8), "rss", "oracle"]
    assert result["peak_rss_bytes"] == 10 * 1024
    assert result["peak_increase_bytes"] == 9 * 1024
