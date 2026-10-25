"""Identity snapshot acceleration preserves codec lookup and Python observers."""

from __future__ import annotations

import json
import sys

import pytest

from unstd.serde import jsonx


@pytest.mark.parametrize("decline", [False, True])
def test_identity_resolves_the_encoder_before_native_or_python_validation(
    monkeypatch, decline
):
    events = []

    class Encoder:
        generation = "before"

        @property
        def encode(self):
            selected = self.generation
            events.append(("lookup", selected))

            def encode_snapshot(snapshot):
                events.append(("encode", selected))
                return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))

            return encode_snapshot

    encoder = Encoder()
    original = jsonx._identity_snapshot

    def native(value):
        events.append(("native", encoder.generation))
        encoder.generation = "after"
        return NotImplemented if decline else original(value, set())

    def python(value, active):
        events.append(("python", encoder.generation))
        return original(value, active)

    monkeypatch.setattr(jsonx, "_IDENTITY", encoder)
    monkeypatch.setattr(jsonx, "_native_identity_snapshot", native)
    monkeypatch.setattr(jsonx, "_identity_snapshot", python)
    monkeypatch.setattr(jsonx, "_HAVE_ORJSON", True)
    assert jsonx.canonical_strict({"x": [1]}) == b'{"x":[1]}'
    assert events[0] == ("lookup", "before")
    assert events[1] == ("native", "before")
    assert events[-1] == ("encode", "before")
    if decline:
        assert ("python", "after") in events


def test_identity_python_walker_remains_visible_to_profilers():
    calls = []
    expected_code = jsonx._identity_snapshot.__code__

    def profile(frame, event, argument):
        if event == "call" and frame.f_code is expected_code:
            calls.append(frame.f_code)

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        assert jsonx.canonical_strict({"x": [1]}) == b'{"x":[1]}'
    finally:
        sys.setprofile(previous)
    assert len(calls) >= 3


def test_identity_uses_a_replaced_python_walker(monkeypatch):
    original = jsonx._identity_snapshot
    calls = []

    def observed(value, active):
        calls.append(value)
        return original(value, active)

    monkeypatch.setattr(jsonx, "_identity_snapshot", observed)
    assert jsonx.canonical_strict({"x": [1]}) == b'{"x":[1]}'
    assert len(calls) >= 3


def test_identity_observes_a_replaced_finiteness_check(monkeypatch):
    calls = []

    def reject(value):
        calls.append(value)
        return False

    monkeypatch.setattr(jsonx, "isfinite", reject)
    with pytest.raises(ValueError, match="finite"):
        jsonx.canonical_strict([1.5])
    assert calls == [1.5]


def test_identity_observes_module_builtin_shadows(monkeypatch):
    calls = []

    def observed_type(value):
        calls.append(value)
        return type(value)

    monkeypatch.setattr(jsonx, "type", observed_type, raising=False)
    assert jsonx.canonical_strict({"x": [1]}) == b'{"x":[1]}'
    assert len(calls) >= 3


def test_identity_does_not_truth_test_backend_configuration(monkeypatch):
    class Configuration:
        def __bool__(self):
            pytest.fail("strict identity invoked a backend configuration hook")

    monkeypatch.setattr(jsonx, "_HAVE_ORJSON", Configuration())
    assert jsonx.canonical_strict({"x": [1]}) == b'{"x":[1]}'
