"""Complete-decoder evidence preserves caller semantics and explicit target failure."""

from __future__ import annotations

import ast
import builtins
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tests.support.environment.backends import require_backend


REPO_ROOT = Path(__file__).resolve().parents[5]
_SPEC = importlib.util.spec_from_file_location(
    "_ragged_gate_profile", REPO_ROOT / "bench/profiles/ragged.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
profile = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = profile
_SPEC.loader.exec_module(profile)


_CLASS = """@dataclass(frozen=True, slots=True)
class TokenIndex:
    tokens: object
    offsets: object
    identity: str
    _device: object = field(default=None, init=False)

    def __post_init__(self):
        if (
            self.tokens.dtype != np.float32
            or self.tokens.ndim != 2
            or self.tokens.shape[1] != _DIMENSION
            or not self.tokens.flags.c_contiguous
            or self.offsets.dtype != np.int64
            or self.offsets.ndim != 1
            or not len(self.offsets)
            or self.offsets[0] != 0
            or self.offsets[-1] != len(self.tokens)
            or (np.diff(self.offsets) <= 0).any()
            or not np.isfinite(self.tokens).all()
            or not self.identity
        ):
            message = 'fixture numeric rejection'
            raise ValueError(message)
        object.__setattr__(self, 'tokens', np.frombuffer(self.tokens.tobytes(), dtype=np.float32).reshape(self.tokens.shape))
        object.__setattr__(self, 'offsets', np.frombuffer(self.offsets.tobytes(), dtype=np.int64))

    @property
    def nbytes(self):
        return self.tokens.nbytes + self.offsets.nbytes

    @property
    def device_nbytes(self):
        return 0
"""
_FUNCTION = """def _remote_index(body, remaining):
    if len(body) < 16 or (len(body) - 16) % (_DIMENSION * 4):
        raise ValueError('fixture shape rejection')
    if len(body) - 16 > remaining:
        raise ValueError('fixture publication rejection')
    return TokenIndex(
        np.frombuffer(body[16:], dtype='<f4').reshape(-1, _DIMENSION),
        np.frombuffer(body[:16], dtype='<i8'),
        _IDENTITY,
    )
"""


@pytest.fixture
def consumer():
    require_backend("numpy")
    from unstd.pack import binpack, ragged

    capture = {
        "definitions": {"TokenIndex": _CLASS, "_remote_index": _FUNCTION},
        "source_path": "fixture.py",
        "dimension": 2,
        "identity": "fixture-model",
    }
    baseline = profile.profile._compile_consumers(capture, binpack)["baseline"]
    decoder = ragged.RaggedDecoder(
        "<f4", columns=2, index_dtype="<i8", finite=True, allow_empty=False
    )
    return capture, baseline, decoder, ragged


def test_candidate_retains_exact_guards_and_exact_result_class(consumer, monkeypatch):
    capture, baseline, decoder, _ = consumer
    saved = []

    def compiled(tree, *args, **kwargs):
        saved.extend(tree.body[0].body[:2])
        return builtins.compile(tree, *args, **kwargs)

    monkeypatch.setattr(profile, "compile", compiled, raising=False)
    candidate = profile._candidate(capture, baseline, decoder)
    original = ast.parse(_FUNCTION).body[0].body[:2]
    assert [ast.dump(node) for node in saved] == [ast.dump(node) for node in original]
    fixture = profile.profile._fixture(4, capture)
    actual = candidate._remote_index(fixture.body, len(fixture.tokens))
    assert type(actual) is baseline.TokenIndex
    profile._check(actual, fixture, baseline.TokenIndex, shared=True)


def test_admission_preserves_all_errors_including_late_values_and_identity(consumer):
    capture, baseline, decoder, _ = consumer
    modules = {
        "baseline": baseline,
        "candidate": profile._candidate(capture, baseline, decoder),
    }
    observed = profile._admission(modules, profile.profile._fixture(4, capture))
    assert observed["baseline"] == observed["candidate"]
    assert {
        "late-nan",
        "late-positive-infinity",
        "late-negative-infinity",
        "empty-identity",
    } <= observed["candidate"].keys()


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("not np.isfinite(self.tokens).all()", "(self.tokens < 0).any()"),
        ("self.tokens.tobytes()", "self.tokens.tobytes(order='F')"),
    ],
)
def test_numeric_contract_drift_prevents_unsafe_factory_adoption(consumer, old, new):
    capture, baseline, decoder, _ = consumer
    capture["definitions"]["TokenIndex"] = _CLASS.replace(old, new)
    with pytest.raises(ValueError, match="reviewed migration"):
        profile._candidate(capture, baseline, decoder)


def test_immutable_copy_cannot_pass_the_zero_copy_complete_oracle(consumer):
    capture, baseline, _, _ = consumer
    fixture = profile.profile._fixture(4, capture)
    copied = baseline._remote_index(fixture.body, len(fixture.tokens))
    with pytest.raises(AssertionError, match="unexpected source storage"):
        profile._check(copied, fixture, baseline.TokenIndex, shared=True)


def test_native_proof_counts_every_repeated_call_and_late_rejection(consumer):
    capture, baseline, decoder, ragged = consumer
    if ragged._native is None:
        pytest.skip("native pack validator not built")
    candidate = profile._candidate(capture, baseline, decoder)
    native = profile._native_proof(
        ragged, candidate, profile.profile._fixture(4, capture)
    )
    assert native["available"]
    assert len(native["validation_calls"]) == 3
    assert all(
        call["finite"] is True and call["allow_empty"] is False
        for call in native["validation_calls"]
    )


def test_target_uses_observed_conservative_bound():
    samples = {
        "baseline": [
            {"wall_ns_per_call": value, "cpu_ns_per_call": value}
            for value in (1000, 2000, 3000)
        ],
        "candidate": [
            {"wall_ns_per_call": value, "cpu_ns_per_call": value}
            for value in (10, 10, 20)
        ],
    }
    result = profile._summary(samples, 100)
    assert result["median_target_met"]
    assert result["observed_conservative_wall_speedup"] == 50
    assert not result["target_met"]


def test_failed_target_writes_evidence_before_exit_one(tmp_path, monkeypatch):
    output = tmp_path / "failed-target.json"
    evidence = {
        "all_targets_met": False,
        "target_speedup": 100,
        "results": {
            "fixture": {
                "baseline_median_wall_ns": 100,
                "candidate_median_wall_ns": 50,
                "median_wall_speedup": 2,
                "target_met": False,
            }
        },
    }
    monkeypatch.setattr(profile, "_run", lambda _: evidence)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ragged",
            "--capture",
            "fixture.json",
            "--enforce-target",
            "--output",
            str(output),
        ],
    )
    assert profile.main() == 1
    assert json.loads(output.read_text()) == evidence


@pytest.mark.parametrize("value", ["nan", "inf", "0"])
def test_nonfinite_or_nonpositive_target_is_rejected(value, monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ragged",
            "--capture",
            "fixture.json",
            "--target-speedup",
            value,
            "--output",
            str(tmp_path / "out.json"),
        ],
    )
    with pytest.raises(SystemExit) as rejected:
        profile.main()
    assert rejected.value.code == 2
