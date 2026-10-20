"""Reinitializing the extension keeps earlier functions and helpers valid."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


pytest.importorskip("unstd.serde._json_native")

_ALIAS = """
import importlib.util
import json
import sys
from unstd.serde import jsonx
from unstd.serde import _json_native as original

document = b'{"value":1}\\n'
jsonx.loads("null")
assert original.loads_lines(document) == [{"value": 1}]
spec = importlib.util.spec_from_file_location(
    "serde_alias._json_native", original.__file__
)
alias = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = alias
spec.loader.exec_module(alias)
assert alias is not original
saved_helper = jsonx._native_dumpb
jsonx._native_dumpb = alias.dumpb
try:
    alias.configure(jsonx.__dict__)
finally:
    jsonx._native_dumpb = saved_helper
assert not alias.is_bound(jsonx.loads)
assert not alias.is_bound(jsonx.dumpb)
"""


def _isolated(script: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed source in an isolated worker
        [sys.executable, "-c", textwrap.dedent(_ALIAS + script)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_alias_configuration_does_not_capture_another_native_fallback() -> None:
    _isolated("""
assert jsonx.loads(s='{"value":1}', parse_int=str) == {"value": "1"}
assert jsonx.dumpb({"z": 1, "a": 2}, sort_keys=True) == b'{"a":2,"z":1}'
errors = []
for decoder in (json.loads, jsonx.loads):
    try:
        decoder('{"value":')
    except json.JSONDecodeError as error:
        errors.append((error.msg, error.doc, error.pos))
assert len(errors) == 2 and errors[0] == errors[1]
try:
    jsonx.dumpb(object())
except TypeError:
    pass
else:
    raise AssertionError("unsupported values must retain the Python fallback")
""")


def test_alias_initialization_preserves_earlier_helper_configuration_checks() -> None:
    _isolated("""
assert original.loads_lines(document) == [{"value": 1}]
decoder = jsonx._decode
jsonx._decode = lambda value: {"replacement": True}
assert original.loads_lines(document) is NotImplemented
jsonx._decode = decoder
assert original.loads_lines(document) == [{"value": 1}]
jsonx._HAVE_MSGSPEC = False
assert original.loads_lines(document) is NotImplemented
jsonx._HAVE_MSGSPEC = True
assert original.loads_lines(document) == [{"value": 1}]
jsonx._HAVE_ORJSON = False
assert original.dumpb({}, False, None, False) is NotImplemented
""")


def test_reconfiguring_an_earlier_instance_keeps_live_configuration_checks() -> None:
    _isolated("""
original.configure(jsonx.__dict__)
jsonx.loads("null")
assert original.dispatch_ready(jsonx.loads)
decoder = jsonx._decode
calls = []
def spy(source):
    calls.append(source)
    return decoder(source)
jsonx._decode = spy
assert jsonx.loads("[1]") == [1]
assert calls == ["[1]"]
assert not original.dispatch_ready(jsonx.loads)
jsonx._decode = decoder
assert original.dispatch_ready(jsonx.loads)
jsonx._decode = len
original.configure(jsonx.__dict__)
assert jsonx.loads('"hello"') == 7
assert not original.dispatch_ready(jsonx.loads)
jsonx._decode = decoder
jsonx.loads("null")
assert original.dispatch_ready(jsonx.loads)
""")
