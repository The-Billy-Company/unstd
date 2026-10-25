"""Captured packing evidence rejects source drift, copies and malformed results."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import struct
import sys

import pytest

from tests.support.environment.backends import require_backend


REPO_ROOT = Path(__file__).resolve().parents[5]
_SPEC = importlib.util.spec_from_file_location(
    "_pack_consumers_gate_profile",
    REPO_ROOT / "bench" / "profiles" / "pack_consumers.py",
)
assert _SPEC is not None
assert _SPEC.loader is not None
profile = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = profile
_SPEC.loader.exec_module(profile)


_DEFINITIONS = {
    "TokenIndex": """@dataclass
class TokenIndex:
    tokens: object
    offsets: object
    identity: str
    def original_method(self):
        return np.frombuffer(self.tokens.tobytes(), dtype='<f4')
""",
    "_remote_index": """def _remote_index(body, remaining):
    if len(body) - 16 > remaining:
        raise ValueError('fixture guard')
    return TokenIndex(
        np.frombuffer(body[16:], dtype='<f4').reshape(-1, _DIMENSION),
        np.frombuffer(body[:16], dtype='<i8'),
        _IDENTITY,
    )
""",
}


def test_transform_changes_only_reviewed_calls_and_retains_caller_guards():
    before, after = profile._transformed({"definitions": _DEFINITIONS})
    assert ast.dump(before.body[1]) == ast.dump(after.body[1])
    assert ast.dump(before.body[2].body[0]) == ast.dump(after.body[2].body[0])
    generated = ast.unparse(after)
    assert "binpack.unpack_array('<f4', body, offset=16)" in generated
    assert "binpack.unpack_array('<i8', body, count=2)" in generated
    assert "np.asarray(" in generated
    assert "np.frombuffer(self.tokens.tobytes()" in generated
    assert "binpack" not in ast.unparse(before)


@pytest.mark.parametrize(
    ("old", "new"),
    [("body[16:]", "body[20:]"), ("dtype='<i8'", "dtype='<u8'")],
)
def test_transform_rejects_drifted_slice_or_dtype(old, new):
    definitions = {
        **_DEFINITIONS,
        "_remote_index": _DEFINITIONS["_remote_index"].replace(old, new),
    }
    with pytest.raises(ValueError, match="changed 1 calls instead of two"):
        profile._transformed({"definitions": definitions})


def test_capture_digest_rejects_edited_source(tmp_path):
    payload = {"schema": 1, "definitions": _DEFINITIONS}
    digest = profile._digest(profile._canonical(payload))
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({**payload, "capture_sha256": digest}))
    assert profile._read_capture(path)["capture_sha256"] == digest
    payload["definitions"] = {**_DEFINITIONS, "TokenIndex": "class Changed: pass\n"}
    path.write_text(json.dumps({**payload, "capture_sha256": digest}))
    with pytest.raises(ValueError, match="digest or schema differs"):
        profile._read_capture(path)


def test_capture_retains_decorators_and_original_source_hash(tmp_path):
    path = tmp_path / profile._CONSUMER
    path.parent.mkdir(parents=True)
    source = "_DIMENSION: Final = 128\n" + "\n".join(_DEFINITIONS.values())
    path.write_text(source)
    path.with_suffix(".json").write_text('{"identity":"fixture-model"}')
    captured = profile._capture(tmp_path)
    assert captured["source_sha256"] == profile._digest(source)
    assert captured["definitions"]["TokenIndex"].startswith("@dataclass\n")
    assert captured["dimension"] == 128
    assert captured["identity"] == "fixture-model"


@pytest.fixture
def numeric():
    np = require_backend("numpy")
    fixture = profile._fixture(4, {"dimension": 8, "identity": "fixture"})
    tokens = np.frombuffer(fixture.body, dtype="<f4", offset=16).reshape(4, 8)
    offsets = np.frombuffer(fixture.body, dtype="<i8", count=2)
    return np, fixture, tokens, offsets


def test_selection_oracle_rejects_adding_a_payload_copy(numeric):
    np, fixture, tokens, offsets = numeric
    profile._check_leaf((tokens, offsets), fixture, shared=True)
    copied = np.frombuffer(tokens.tobytes(), dtype="<f4").reshape(tokens.shape)
    with pytest.raises(AssertionError, match="unexpected source storage"):
        profile._check_leaf((copied, offsets), fixture, shared=True)


def test_result_oracle_rejects_mutability_and_wrong_values(numeric):
    np, fixture, tokens, offsets = numeric
    profile._check_arrays(tokens, offsets, fixture)
    with pytest.raises(AssertionError, match="independent struct oracle"):
        profile._check_arrays(tokens.copy(), offsets, fixture)
    corrupt = np.frombuffer(bytes(len(fixture.tokens)), dtype="<f4").reshape(
        tokens.shape
    )
    with pytest.raises(AssertionError, match="independent struct oracle"):
        profile._check_arrays(corrupt, offsets, fixture)
    bad_offsets = np.frombuffer(struct.pack("<2q", 1, fixture.rows), dtype="<i8")
    with pytest.raises(AssertionError, match="independent struct oracle"):
        profile._check_arrays(tokens, bad_offsets, fixture)


def test_wrong_result_never_reaches_the_timer(monkeypatch):
    def no_timing(*_args):
        pytest.fail("incorrect result reached operation timing")

    def reject(_value):
        message = "fixture oracle rejected"
        raise AssertionError(message)

    monkeypatch.setattr(profile, "_batch", no_timing)
    with pytest.raises(AssertionError, match="fixture oracle rejected"):
        profile._measure(lambda: "incorrect", reject, 1)
