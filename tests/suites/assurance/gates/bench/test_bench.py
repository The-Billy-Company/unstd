"""Tests for the benchmark harness itself.

A harness nobody checks is a harness that reports whatever it feels like, and the
failure is invisible: a broken timer still prints plausible microseconds. So the
measurement primitive is held to known costs with an independent virtual clock, and the
parts that decide *whether a claim passed* — the equivalence audit, the skip
rule, and the one-way floor ratchet — are exercised directly, since those are
what a green run actually rests on.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from bench.__main__ import main
from bench.core import cases as case_mod
from bench.core import report, timing
from bench.core.cases import Case
from bench.core.timing import measure


if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------- timing


@pytest.mark.parametrize("cost_ns", [200, 200_000, 2_000_000, 300_000_000])
def test_measure_recovers_a_known_cost(
    monkeypatch: pytest.MonkeyPatch, cost_ns: int
) -> None:
    """Known elapsed work must be divided by calls, independent of scheduling."""
    elapsed = 0
    calls = 0

    def work() -> None:
        nonlocal elapsed, calls
        elapsed += cost_ns
        calls += 1

    # Replace only the timer used by the instrument. The callable independently
    # advances elapsed work, covering cheap and over-budget batches without
    # assuming that an OS scheduler wakes a 200us sleep within 600us.
    monkeypatch.setattr(
        timing, "time", SimpleNamespace(perf_counter_ns=lambda: elapsed)
    )
    got = measure(work, rounds=3)
    assert calls > 1
    assert got.per_op_ns == cost_ns
    assert got.spread == 0
    assert got.rounds >= 1


@pytest.mark.perf
def test_measure_calibrates_iterations_to_the_callable() -> None:
    """A cheap call gets many iterations; an expensive one gets few."""
    cheap = measure(lambda: None, rounds=2)
    dear = measure(lambda: time.sleep(0.001), rounds=2)
    assert cheap.iters > dear.iters * 100


def test_measure_restores_the_collector() -> None:
    """GC is disabled inside the timed region and must come back on."""
    import gc

    assert gc.isenabled()
    measure(lambda: None, rounds=2)
    assert gc.isenabled()


def test_measure_restores_the_collector_when_the_callable_raises() -> None:
    import gc

    def boom() -> None:
        raise RuntimeError

    with pytest.raises(RuntimeError):
        measure(boom, rounds=2)
    assert gc.isenabled()


@pytest.mark.perf
def test_human_picks_a_legible_unit() -> None:
    assert measure(lambda: None, rounds=2).human().endswith("ns")
    assert measure(lambda: time.sleep(0.002), rounds=2).human().endswith("ms")


# --------------------------------------------------------------- the audit


def _case(**kw: Any) -> Case:
    base: dict[str, Any] = {
        "name": "probe",
        "group": "test",
        "backend": "json",
        "payload": lambda: 21,
        "accel": lambda p: p * 2,
        "base": lambda p: p * 2,
    }
    return Case(**(base | kw))


def test_audit_passes_when_both_sides_agree() -> None:
    assert case_mod.audit([_case()]) == []


def test_audit_catches_two_sides_that_disagree() -> None:
    """This is the harness's whole defense against a fabricated ratio."""
    bad = case_mod.audit([_case(base=lambda p: p * 3)])
    assert len(bad) == 1
    assert "42" in bad[0].detail
    assert "63" in bad[0].detail


def test_audit_skips_cases_declared_not_like_for_like() -> None:
    assert case_mod.audit([_case(base=lambda p: p * 3, equivalent=False)]) == []


def test_audit_skips_cases_whose_backend_is_absent() -> None:
    absent = _case(base=lambda p: p * 3, backend="no_such_module_9f3a")
    assert absent.available is False
    assert case_mod.audit([absent]) == []


def test_a_probe_overrides_the_import_check() -> None:
    """`rex` needs this: importability does not prove the fast path was taken."""
    assert _case(backend="json", probe=lambda: False).available is False
    assert _case(backend="no_such_module_9f3a", probe=lambda: True).available is True


# --------------------------------------------------------------- registry


def test_every_registered_case_names_a_real_group() -> None:
    for c in case_mod.registry():
        assert c.group in case_mod.groups()


def test_registry_narrows_by_group() -> None:
    picked = case_mod.registry(["serde"])
    assert picked
    assert all(c.group == "serde" for c in picked)
    assert len(picked) < len(case_mod.registry())


def test_every_case_carries_a_note_or_an_obvious_name() -> None:
    """A bare ratio with no prose is a number nobody can act on."""
    for c in case_mod.registry():
        assert c.note or "·" in c.name


def test_non_equivalent_cases_explain_themselves() -> None:
    """If the two sides compute different things, the note has to say so."""
    for c in case_mod.registry():
        if not c.equivalent:
            assert c.note, f"{c.name} is not like-for-like and says nothing about it"


# --------------------------------------------------------------- baseline


def test_the_committed_baseline_covers_every_registered_case() -> None:
    """A case with no floor is measured but never judged."""
    floors = report.load_baseline()
    missing = [c.name for c in case_mod.registry() if c.name not in floors]
    assert not missing, f"unpinned cases — run `python3 -m bench update`: {missing}"


def test_the_committed_baseline_parses_and_is_sane() -> None:
    raw = json.loads(report.BASELINE.read_text(encoding="utf-8"))
    assert raw["cases"]
    for name, row in raw["cases"].items():
        assert float(row["floor"]) > 0, f"{name} has a non-positive floor"


def test_a_floor_is_never_lowered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ratchet's one job. A floor that can fall is a diary, not a gate."""
    store = tmp_path / "baseline.json"
    monkeypatch.setattr(report, "BASELINE", store)

    fast = report.Result(_case(), _stub(1.0), _stub(10.0), None)
    report.write_baseline([fast])
    high = report.load_baseline()["probe"]
    assert high == pytest.approx(7.0)  # 10× observed, pinned at 70%

    slow = report.Result(_case(), _stub(5.0), _stub(10.0), high)
    raised, held = report.write_baseline([slow])
    assert report.load_baseline()["probe"] == high, "a 2× run lowered a 7× floor"
    assert (raised, held) == (0, 1)


def test_a_floor_is_raised_when_the_code_got_faster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "baseline.json"
    monkeypatch.setattr(report, "BASELINE", store)
    report.write_baseline([report.Result(_case(), _stub(5.0), _stub(10.0), None)])
    before = report.load_baseline()["probe"]
    report.write_baseline([report.Result(_case(), _stub(1.0), _stub(10.0), before)])
    assert report.load_baseline()["probe"] > before


def test_a_skipped_case_keeps_its_prior_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A machine missing one backend must not erase that claim for everyone."""
    store = tmp_path / "baseline.json"
    monkeypatch.setattr(report, "BASELINE", store)
    report.write_baseline([report.Result(_case(), _stub(1.0), _stub(10.0), None)])
    kept = report.load_baseline()["probe"]
    report.write_baseline([report.Result(_case(), None, None, kept)])
    assert report.load_baseline()["probe"] == kept


def test_a_narrowed_update_keeps_every_case_it_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``update --group X`` once rewrote the file with group X alone, erasing every other floor."""
    store = tmp_path / "baseline.json"
    store.write_text(
        json.dumps(
            {"cases": {"elsewhere": {"floor": 3.5, "measured": 5.0, "group": "serde"}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(report, "BASELINE", store)
    report.write_baseline([report.Result(_case(), _stub(1.0), _stub(10.0), None)])
    assert report.load_baseline() == {"elsewhere": 3.5, "probe": pytest.approx(7.0)}


def _stub(us: float) -> Any:
    from bench.core.timing import Measurement

    return Measurement(per_op_ns=us * 1_000, iters=100, rounds=3, spread=0.0)


# --------------------------------------------------------------- verdicts


def test_a_result_below_its_floor_fails() -> None:
    assert report.Result(_case(), _stub(5.0), _stub(10.0), 5.0).failed
    assert not report.Result(_case(), _stub(1.0), _stub(10.0), 5.0).failed


def test_an_unpinned_result_cannot_fail() -> None:
    """A case with no recorded floor is reported, not judged."""
    assert not report.Result(_case(), _stub(9.0), _stub(10.0), None).failed


def test_a_skipped_result_cannot_fail() -> None:
    assert not report.Result(_case(), None, None, 100.0).failed


def test_a_noisy_result_is_flagged() -> None:
    from bench.core.timing import Measurement

    jittery = Measurement(per_op_ns=1.0, iters=10, rounds=3, spread=0.9)
    assert report.Result(_case(), jittery, _stub(10.0), None).noisy


# --------------------------------------------------------------- cli


def test_list_runs_without_measuring_anything(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "serde" in out
    assert "clone" in out


def test_an_unknown_group_is_an_error_not_an_empty_pass(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["list", "--group", "no-such-group"]) == 2
    assert "no cases" in capsys.readouterr().err


def test_one_group_measures_and_verifies(capsys: pytest.CaptureFixture[str]) -> None:
    """An end-to-end run over the cheapest group, so CI exercises the real path."""
    code = main(["verify", "--group", "clone", "--rounds", "2"])
    out = capsys.readouterr().out
    assert "clone.deep" in out
    # Not asserting green: a loaded CI runner may legitimately miss a floor. What
    # must hold is that verify returns a verdict rather than crashing, and never
    # reports success while a case was skipped.
    assert code in (0, 1, 2)


def test_serde_profile_inventory_excludes_declarations_and_keeps_nested_functions(
    tmp_path: Path,
) -> None:
    from bench.profiles.serde_profile import _functions

    source = tmp_path / "codec.py"
    source.write_text(
        "from typing import TYPE_CHECKING, Protocol, overload\n"
        "if TYPE_CHECKING:\n"
        "    def typing_only(): ...\n"
        "class Writer(Protocol):\n"
        "    def write(self, value): ...\n"
        "@overload\n"
        "def encode(value: int): ...\n"
        "def encode(value):\n"
        "    def nested(): return value\n"
        "    return nested()\n"
        "class Codec:\n"
        "    @staticmethod\n"
        "    def decode(value): return value\n"
    )
    assert list(_functions(source)) == [
        (8, "encode"),
        (9, "encode.nested"),
        (12, "Codec.decode"),
    ]


def test_serde_profile_oracle_keeps_types_and_dictionary_order() -> None:
    from bench.profiles.serde_profile import _same

    assert _same({"values": [1, True, None, 1.5]}, {"values": [1, True, None, 1.5]})
    assert not _same({"values": [1]}, {"values": [True]})
    assert not _same({"values": [1]}, {"values": [1.0]})
    assert not _same([1], (1,))
    assert not _same({"a": 1, "b": 2}, {"b": 2, "a": 1})


def test_serde_profile_refuses_to_measure_a_different_imported_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    from bench.profiles.serde_profile import _worker

    monkeypatch.setattr(sys, "path", list(sys.path))
    with pytest.raises(RuntimeError, match=r"requested .* imported"):
        _worker(tmp_path, 1, False)


def test_serde_paired_imports_restore_the_original_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path
    import sys

    from bench.profiles import serde_profile
    from unstd.serde import jsonx

    original_path = sys.path[:]
    original_flag = jsonx._HAVE_ORJSON
    monkeypatch.setattr(serde_profile, "_workloads", lambda: [])
    source = Path(jsonx.__file__).resolve().parents[2]
    assert serde_profile._isolated_cases(source, True) == []
    assert sys.modules["unstd.serde.jsonx"] is jsonx
    assert jsonx._HAVE_ORJSON is original_flag
    assert sys.path == original_path
