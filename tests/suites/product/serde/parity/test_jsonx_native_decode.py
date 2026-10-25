"""Native calls preserve the existing Python decoder's observable behavior."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
import textwrap
from types import FunctionType
from typing import Any

from packaging.tags import sys_tags
import pytest

from unstd.serde import jsonx


native = pytest.importorskip("unstd.serde._json_native")
pytestmark = pytest.mark.skipif(
    sys.version_info[:2] != (3, 14), reason="function dispatch targets CPython 3.14"
)


def _ready() -> None:
    jsonx.loads("null")  # Refresh instrumentation after earlier profiling.
    assert native.is_bound(jsonx.loads)
    assert native.dispatch_ready(jsonx.loads)


def test_native_dispatch_retains_python_function_metadata() -> None:
    _ready()
    assert inspect.isfunction(jsonx.loads)
    assert inspect.signature(jsonx.loads) == inspect.signature(json.loads)
    assert jsonx.loads.__wrapped__ is json.loads
    assert jsonx.loads.__name__ == "loads"


def test_profiled_calls_keep_python_frames_and_resume_acceleration() -> None:
    _ready()
    observed: list[str] = []
    code = jsonx.loads.__code__

    def profile(frame: Any, event: str, _argument: object) -> None:
        if frame.f_code is code:
            observed.append(event)

    previous = sys.getprofile()
    try:
        sys.setprofile(profile)
        assert jsonx.loads('{"value": 7}') == {"value": 7}
    finally:
        sys.setprofile(previous)
    assert "call" in observed
    assert "return" in observed
    _ready()


@pytest.mark.parametrize("local", [False, True])
def test_monitoring_observes_the_first_call_and_can_be_disabled(local: bool) -> None:
    _ready()
    monitoring = sys.monitoring
    available = [tool for tool in range(6) if monitoring.get_tool(tool) is None]
    if not available:
        pytest.skip("all monitoring tool IDs are already occupied")
    tool = available[-1]
    monitoring.use_tool_id(tool, "unstd decoder parity")
    observed: list[object] = []

    def started(code: object, _offset: int) -> None:
        observed.append(code)

    try:
        monitoring.register_callback(tool, monitoring.events.PY_START, started)
        if local:
            monitoring.set_local_events(
                tool, jsonx.loads.__code__, monitoring.events.PY_START
            )
        else:
            monitoring.set_events(tool, monitoring.events.PY_START)
        assert jsonx.loads("[1,2]") == [1, 2]
        assert jsonx.loads.__code__ in observed
    finally:
        monitoring.set_events(tool, 0)
        monitoring.set_local_events(tool, jsonx.loads.__code__, 0)
        monitoring.free_tool_id(tool)
    _ready()


def test_replaced_decoder_globals_are_observed_and_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready()
    calls: list[object] = []
    original = jsonx._decode

    def decode(source: object) -> object:
        calls.append(source)
        return original(source)

    with monkeypatch.context() as patch:
        patch.setattr(jsonx, "_decode", decode)
        assert jsonx.loads("[1]") == [1]
        assert calls == ["[1]"]
        assert not native.dispatch_ready(jsonx.loads)
    _ready()


def test_replaced_function_code_uses_normal_python_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready()

    def replacement(source: object, **keywords: object) -> object:
        return source, keywords

    try:
        with monkeypatch.context() as patch:
            patch.setattr(jsonx.loads, "__code__", replacement.__code__)
            assert jsonx.loads("{}", marker=7) == ("{}", {"marker": 7})
    finally:
        native.configure(jsonx.__dict__)
    _ready()


def test_replaced_function_defaults_use_normal_argument_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(jsonx.loads, "__defaults__", ("null",))
            assert jsonx.loads() is None
            assert jsonx.loads("[7]") == [7]
            assert not native.dispatch_ready(jsonx.loads)
    finally:
        native.configure(jsonx.__dict__)
    _ready()


def test_exhausted_dict_watcher_slots_keep_exact_fallback_guards() -> None:
    source = textwrap.dedent(
        """
        import ctypes
        callback_type = ctypes.CFUNCTYPE(
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p
        )
        callback = callback_type(lambda *_: 0)
        add = ctypes.pythonapi.PyDict_AddWatcher
        add.argtypes = (callback_type,)
        add.restype = ctypes.c_int
        allocated = []
        while True:
            try:
                allocated.append(add(callback))
            except RuntimeError:
                break
        from unstd.serde import jsonx
        assert jsonx.loads('{"x":1}') == {"x":1}
        seen = []
        jsonx._decode = lambda value: seen.append(value) or {"changed":True}
        assert jsonx.loads('{}') == {"changed":True}
        assert seen == ['{}']
        clear = ctypes.pythonapi.PyDict_ClearWatcher
        clear.argtypes = (ctypes.c_int,)
        clear.restype = ctypes.c_int
        for watcher in allocated:
            assert clear(watcher) == 0
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed source in an isolated worker
        [sys.executable, "-c", source],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_reconfiguration_does_not_bless_replacement_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready()
    replacement = FunctionType(
        (lambda source: {"wrapped": source}).__code__, jsonx.__dict__
    )
    with monkeypatch.context() as patch:
        patch.setattr(jsonx, "loads", replacement)
        native.configure(jsonx.__dict__)
        assert jsonx.loads("1") == {"wrapped": "1"}
    _ready()


def test_reconfiguration_does_not_bless_replacement_builtin_decoders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ready()
    with monkeypatch.context() as patch:
        patch.setattr(jsonx, "_decode", len)
        native.configure(jsonx.__dict__)
        assert jsonx.loads('"hello"') == 7
        assert not native.dispatch_ready(jsonx.loads)
    _ready()


def test_reconfiguration_rejects_a_copy_of_function_globals() -> None:
    _ready()
    with pytest.raises(ValueError, match="actual globals"):
        native.configure(dict(jsonx.__dict__))
    _ready()


@pytest.mark.parametrize("remove_functions", [False, True])
def test_reconfiguration_keeps_the_original_globals_when_a_copy_is_rejected(
    remove_functions: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready()
    copied = dict(jsonx.__dict__)
    if remove_functions:
        copied.pop("loads")
        copied.pop("dumpb")
    with pytest.raises(ValueError, match="actual globals"):
        native.configure(copied)
    with monkeypatch.context() as patch:
        patch.setattr(jsonx, "_decode", lambda _: {"changed": True})
        assert jsonx.loads("{}") == {"changed": True}
    _ready()


def test_small_thread_stack_keeps_discarded_source_recursion_limits() -> None:
    # CPython 3.14 estimates a fixed 4 MB stack on musl instead of querying the
    # real thread stack. With 256 KiB, the stdlib oracle itself segfaults.
    # https://github.com/python/cpython/blob/v3.14.8/Python/ceval.c#L460-L492
    if sys.version_info[:2] == (3, 14) and any(
        tag.platform.startswith("musllinux") for tag in sys_tags()
    ):
        pytest.skip("CPython 3.14's stdlib parser overruns small musl thread stacks")
    source = textwrap.dedent(
        """
        from functools import partial
        import json
        import sys
        import threading
        from unstd.serde import jsonx

        sys.setrecursionlimit(10000)
        threading.stack_size(256 * 1024)
        failures = []

        def parse(function, document):
            try:
                return ("value", function(document))
            except Exception as error:
                return ("error", type(error), str(error))

        def document_at(depth):
            return ('{"discarded":' + '[' * depth + '0' + ']' * depth
                    + ',"discarded":0,"padding":"' + 'x' * 2049 + '"}')

        def worker():
            # Match the C-call context: inline Python CALL and vectorcall can
            # consume different machine-stack space at the exact boundary.
            reference = partial(json.loads)
            candidate = partial(jsonx.loads)
            lower, upper = 0, 6000
            while lower + 1 < upper:
                middle = (lower + upper) // 2
                if parse(reference, document_at(middle))[0] == "value":
                    lower = middle
                else:
                    upper = middle
            depths = (1, 128, 512, 1024, 2048, 6000, *range(lower - 3, upper + 4))
            for depth in depths:
                document = document_at(depth)
                expected = parse(reference, document)
                actual = parse(candidate, document)
                if actual != expected:
                    failures.append((depth, expected, actual))
            for depth in (1, 128, 512, 1024, 2048, 6000):
                document = document_at(depth)
                expected = parse(json.loads, document)
                actual = parse(jsonx.loads, document)
                if actual != expected:
                    failures.append((depth, expected, actual))

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert not failures, failures
        """
    )
    result = subprocess.run(  # noqa: S603 - fixed source in an isolated worker
        [sys.executable, "-c", source],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("size", [0, 1, 2, 5, 6, 16, 86, 256, 512, 4096])
def test_large_unicode_objects_across_dictionary_table_sizes(size: int) -> None:
    _ready()
    value = {
        f"key-\u00e9-\U0001f680-{index}": f"value-{index}" for index in range(size)
    }
    document = " " * 2050 + json.dumps(value, ensure_ascii=False)
    assert jsonx.loads(document) == json.loads(document)


def test_large_duplicate_keys_keep_only_the_last_value_without_losing_errors() -> None:
    _ready()
    document = (
        '{"duplicate":0.84551240822557006,'
        + ",".join(f'"key-{index}":"value-{index}"' for index in range(256))
        + ',"duplicate":"last"}'
    )
    assert jsonx.loads(document) == json.loads(document)


@pytest.mark.parametrize("number", ["0.84551240822557006", "18446744073709551616"])
def test_large_dense_objects_reparse_inexact_native_numbers(number: str) -> None:
    _ready()
    document = '{"padding":"' + "x" * 2050 + '","number":' + number + "}"
    expected = json.loads(document)
    actual = jsonx.loads(document)
    assert type(actual["number"]) is type(expected["number"])
    assert actual == expected


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize(
    "prefix",
    [
        '"value":0.84551240822557006',
        '"nested":{"value":1.00000000000000011102230246251565404236316680908203126}',
        '"escaped\\"1.5":"1.5\\"", "value":-0.0',
    ],
)
def test_large_object_numeric_prefix_preserves_exact_values(
    binary: bool, prefix: str
) -> None:
    _ready()
    document = "{" + prefix + ',"padding":"' + "x" * 3000 + '"}'
    source = document.encode() if binary else document
    assert json.dumps(jsonx.loads(source)) == json.dumps(json.loads(source))
