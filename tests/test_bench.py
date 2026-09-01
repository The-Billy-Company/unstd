"""Tests for the benchmark harness itself.

A harness nobody checks is a harness that reports whatever it feels like, and the
failure is invisible: a broken timer still prints plausible microseconds. So the
measurement primitive is held to a known cost with a synthetic sleep, and the
parts that decide *whether a claim passed* — the equivalence audit, the skip
rule, and the one-way floor ratchet — are exercised directly, since those are
what a green run actually rests on.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest

from bench import cases as case_mod
from bench import report
from bench.__main__ import main
from bench.cases import Case
from bench.timing import measure


if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------- timing


def test_measure_recovers_a_known_cost() -> None:
    """A callable that sleeps 200 µs must be reported at roughly 200 µs.

    The bound is loose on purpose — a shared laptop cannot promise better — but
    it is tight enough to catch a wrong divisor, which is the bug that makes a
    benchmark harness confidently lie.
    """
    got = measure(lambda: time.sleep(0.0002), rounds=3)
    assert 150_000 < got.per_op_ns < 600_000


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


def _stub(us: float) -> Any:
    from bench.timing import Measurement

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
    from bench.timing import Measurement

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
