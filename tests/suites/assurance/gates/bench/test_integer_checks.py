"""Integer evidence isolates implementations, retains routes and detects mutation."""

import os
import py_compile
from types import SimpleNamespace

import pytest

from tests.support.environment.backends import require_backend


@pytest.fixture
def profile():
    require_backend("numpy")
    from bench.profiles import integer_checks

    return integer_checks


def test_retained_source_is_independent_from_current_package_globals(
    profile, monkeypatch
):
    previous = profile._load_previous(profile._FIRST_PASS)
    monkeypatch.setattr(profile.numeric, "within", lambda *args, **kwargs: False)
    assert previous([1], [1], atol=0)
    assert not previous([1], [2], atol=0)
    assert not profile.numeric.within([1], [1], atol=0)


def test_consumer_uses_the_selected_real_backend_and_routes_restore_on_failure(profile):
    np = require_backend("numpy")
    fixture = profile.Fixture("one", np.asarray([1]), np.asarray([1]), 0)
    original = profile.numeric.within
    calls = []

    def selected(actual, expected, *, atol):
        calls.append((actual, expected, atol))
        return True

    class Consumer:
        @staticmethod
        def close(actual, expected, *, rtol, atol):
            assert rtol == 0
            assert profile.numeric.within(actual, expected, atol=atol)

    assert profile._prove_consumer(Consumer(), fixture, True, selected) == {
        "within_calls": 1,
        "native_calls": 0,
        "native_declines": 0,
    }
    assert len(calls) == 1
    assert profile.numeric.within is original

    def interrupt():
        with profile._route(selected):
            assert profile.numeric.within is selected
            message = "interrupted"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="interrupted"):
        interrupt()
    assert profile.numeric.within is original


def test_fixture_generation_does_not_rewrite_the_preceding_mixed_case(profile):
    fixtures = {fixture.name: fixture for fixture in profile._fixtures()}
    accepted = fixtures["mixed-extrema/4096"]
    rejected = fixtures["mixed-carry-rejected/4096"]
    assert accepted.oracle()
    assert not rejected.oracle()
    assert int(accepted.right[1]) == 2**64 - 2
    assert int(rejected.right[1]) == 2**64 - 1
    assert not accepted.right.flags.writeable


def test_adverse_fixtures_cover_full_scans_layouts_and_native_widths(profile):
    fixtures = {fixture.name: fixture for fixture in profile._fixtures()}
    for name in ("first", "middle", "last"):
        assert not fixtures[f"mismatch-{name}/4096"].oracle()
    for name in ("matrix", "fortran", "reversed-matrix"):
        fixture = fixtures[f"{name}/4096"]
        assert fixture.left.shape == (64, 64)
        assert fixture.oracle()
    for dtype in ("int8", "uint8", "int16", "uint16", "int32", "uint32"):
        assert fixtures[f"narrow-{dtype}/4096"].oracle()
    assert fixtures["wide-tolerance-accepted/8"].oracle()
    assert not fixtures["wide-tolerance-rejected/8"].oracle()
    assert fixtures["equal/0"].consumer_skip_reason
    assert fixtures["wide-tolerance-accepted/8"].consumer_skip_reason


def test_a_comparison_cannot_mutate_inputs_and_keep_a_passing_speed_report(profile):
    np = require_backend("numpy")
    source = np.asarray([1])
    fixture = profile.Fixture("mutable-owner", source, np.asarray([1]), 0)
    original = profile.numeric.within

    def mutate():
        source.flags.writeable = True
        source[0] += 1
        return True

    with pytest.raises(AssertionError, match="mutated its inputs"):
        profile._measure(
            (
                profile.Lane("legacy", mutate, None),
                profile.Lane("current", lambda: True, original),
            ),
            True,
            3,
            fixture=fixture,
        )
    assert profile.numeric.within is original


def test_invalid_retained_source_does_not_fall_back_to_current_package(
    profile, tmp_path
):
    missing = tmp_path / "missing.py"
    with pytest.raises(FileNotFoundError):
        profile._load_previous(missing)
    invalid = tmp_path / "invalid.py"
    invalid.write_text("within = None\n")
    with pytest.raises(TypeError, match="Previous source must define within"):
        profile._load_previous(invalid)


def test_retained_source_loader_does_not_borrow_stale_bytecode(profile, tmp_path):
    path = tmp_path / "previous.py"
    old = "def within(actual, expected, *, atol):\n    return True \n"
    new = "def within(actual, expected, *, atol):\n    return False\n"
    assert len(old) == len(new)
    path.write_text(old)
    original = path.stat()
    py_compile.compile(str(path), doraise=True)
    path.write_text(new)
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert not profile._load_previous(path)([1], [1], atol=0)


def test_captured_first_pass_cannot_be_changed_without_breaking_its_receipt(
    profile, monkeypatch, tmp_path
):
    path = tmp_path / "previous.txt"
    path.write_text("def within(actual, expected, *, atol):\n    return True\n")
    monkeypatch.setattr(profile, "_FIRST_PASS", path)
    with pytest.raises(AssertionError, match="captured revision"):
        profile._load_previous(path)


@pytest.mark.parametrize("answer", [True, NotImplemented])
def test_native_route_proof_distinguishes_success_decline_and_portable(
    profile, monkeypatch, answer
):
    np = require_backend("numpy")
    fixture = profile.Fixture("one", np.asarray([1]), np.asarray([1]), 0)

    def native(actual, expected, atol):
        return answer

    backend = SimpleNamespace(within=native)
    monkeypatch.setattr(profile.numeric, "_native", backend)

    def selected(actual, expected, *, atol):
        active = profile.numeric._native
        result = (
            NotImplemented if active is None else active.within(actual, expected, atol)
        )
        return True if result is NotImplemented else result

    proof = profile._prove_consumer(None, fixture, True, selected)
    assert proof == {
        "within_calls": 1,
        "native_calls": int(answer is True),
        "native_declines": int(answer is NotImplemented),
    }
    assert profile._prove_consumer(None, fixture, True, selected, portable=True) == {
        "within_calls": 1,
        "native_calls": 0,
        "native_declines": 0,
    }
    assert profile.numeric._native is backend
