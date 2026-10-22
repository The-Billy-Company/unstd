"""Faults in caller-owned streams propagate unchanged and leave the module usable.

Fixture-owned handles fail on a chosen read, write or flush. The contract under
test: the injected exception object escapes, no value is returned or yielded
past the fault, the handle is never closed by the module, and later calls work.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from unstd import yaml


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


class _InjectedError(OSError):
    """An exception type no backend layer raises on its own."""


class _InjectedErrors:
    """Count stream calls and raise one fixture-owned fault on the chosen call."""

    def __init__(self, data=None, *, fail_on: str = "", at: int = 0):
        super().__init__(data)
        self.fault = _InjectedError(f"injected {fail_on} fault")
        self.fail_on, self.at = fail_on, at
        self.calls = {"read": 0, "write": 0, "flush": 0, "close": 0}

    def _step(self, name: str) -> None:
        self.calls[name] += 1
        if name == self.fail_on and self.calls[name] == self.at:
            raise self.fault

    def read(self, size=-1):
        self._step("read")
        return super().read(size)

    def write(self, data):
        self._step("write")
        return super().write(data)

    def flush(self):
        self._step("flush")
        super().flush()

    def close(self):
        self.calls["close"] += 1
        super().close()


class TextFaulty(_InjectedErrors, io.StringIO):
    pass


class BinaryFaulty(_InjectedErrors, io.BytesIO):
    pass


# Three or more 4 KiB reader chunks, so later read numbers land mid-document.
LARGE = {f"key{i:05}": i for i in range(2000)}
LARGE_TEXT = "".join(f"key{i:05}: {i}\n" for i in range(2000))
STREAM = [{f"doc{i}": [i, f"v{i}"]} for i in range(400)]
STREAM_TEXT = "".join(f"---\ndoc{i}:\n- {i}\n- v{i}\n" for i in range(400))


def _handle(binary: bool, text: str, **fault):
    return BinaryFaulty(text.encode(), **fault) if binary else TextFaulty(text, **fault)


@pytest.mark.parametrize("binary", [False, True])
@pytest.mark.parametrize("at", [1, 2, 3])
def test_load_read_fault_escapes_without_a_value(binary, at):
    handle = _handle(binary, LARGE_TEXT, fail_on="read", at=at)
    with pytest.raises(_InjectedError) as caught:
        yaml.load(handle)
    assert caught.value is handle.fault
    assert handle.calls["read"] == at
    assert handle.calls["close"] == 0
    assert not handle.closed
    assert yaml.load(_handle(binary, LARGE_TEXT)) == LARGE


@pytest.mark.parametrize("binary", [False, True])
def test_load_all_read_fault_ends_the_iterator_after_an_exact_prefix(binary):
    handle = _handle(binary, STREAM_TEXT, fail_on="read", at=2)
    documents = yaml.load_all(handle)
    received = []
    with pytest.raises(_InjectedError) as caught:
        received.extend(documents)
    assert caught.value is handle.fault
    assert received == STREAM[: len(received)]
    assert len(received) < len(STREAM)
    with pytest.raises(StopIteration):
        next(documents)
    assert handle.calls["close"] == 0
    assert list(yaml.load_all(_handle(binary, STREAM_TEXT))) == STREAM


def test_closing_a_load_all_iterator_mid_stream_never_closes_the_handle():
    handle = _handle(False, STREAM_TEXT)
    documents = yaml.load_all(handle)
    assert next(documents) == STREAM[0]
    documents.close()
    assert handle.calls["close"] == 0
    assert not handle.closed


@pytest.mark.parametrize("at", [1, 5, 40])
def test_dump_write_fault_escapes_and_written_text_is_a_prefix(at):
    handle = TextFaulty(fail_on="write", at=at)
    with pytest.raises(_InjectedError) as caught:
        yaml.dump(LARGE, handle)
    assert caught.value is handle.fault
    assert handle.calls["write"] == at
    assert handle.calls["close"] == 0
    healthy = TextFaulty()
    assert yaml.dump(LARGE, healthy) is None
    assert yaml.loads(healthy.getvalue()) == LARGE
    assert healthy.getvalue().startswith(handle.getvalue())
    assert len(handle.getvalue()) < len(healthy.getvalue())


def test_dump_flush_fault_escapes_after_the_document_is_written():
    handle = TextFaulty(fail_on="flush", at=1)
    with pytest.raises(_InjectedError) as caught:
        yaml.dump({"name": "café"}, handle)
    assert caught.value is handle.fault
    assert handle.calls["close"] == 0
    assert yaml.loads(handle.getvalue()) == {"name": "café"}


def test_dump_all_write_fault_in_a_later_document_keeps_earlier_documents():
    healthy = TextFaulty()
    yaml.dump_all(STREAM[:3], healthy)
    first_two = TextFaulty()
    yaml.dump_all(STREAM[:2], first_two)
    handle = TextFaulty(fail_on="write", at=first_two.calls["write"] + 2)
    with pytest.raises(_InjectedError) as caught:
        yaml.dump_all(STREAM[:3], handle)
    assert caught.value is handle.fault
    assert handle.calls["close"] == 0
    assert healthy.getvalue().startswith(handle.getvalue())
    complete = handle.getvalue()[: handle.getvalue().rindex("---")]
    assert list(yaml.load_all(complete)) == STREAM[:2]


def test_dump_all_source_failure_propagates_its_own_exception():
    failure = _InjectedError("document source failed")
    handle = TextFaulty()

    def documents() -> Iterator[object]:
        yield {"first": 1}
        raise failure

    with pytest.raises(_InjectedError) as caught:
        yaml.dump_all(documents(), handle)
    assert caught.value is failure
    assert handle.calls["close"] == 0
    assert list(yaml.load_all(handle.getvalue())) == [{"first": 1}]
    assert yaml.dumps({"after": True}) == "after: true\n"


def test_representer_failure_returns_no_partial_text():
    with pytest.raises(yaml.YAMLError):
        yaml.dumps({"fine": 1, "broken": object()})
    handle = TextFaulty()
    with pytest.raises(yaml.YAMLError):
        yaml.dump_all([{"fine": 1}, object()], handle)
    assert handle.calls["close"] == 0
    assert yaml.dumps({"after": True}) == "after: true\n"


@pytest.mark.parametrize("mode", ["r", "rb"])
def test_real_file_handles_stay_open_for_their_owner(tmp_path: Path, mode):
    path = tmp_path / "config.yaml"
    with path.open("w", encoding="utf-8") as stream:
        assert yaml.dump_all([{"name": "café"}, [1, 2]], stream) is None
        assert not stream.closed
    with path.open(mode, encoding=None if "b" in mode else "utf-8") as stream:
        documents = yaml.load_all(stream)
        assert next(documents) == {"name": "café"}
        assert next(documents) == [1, 2]
        assert not stream.closed
        stream.seek(0)
        with pytest.raises(yaml.YAMLError):
            yaml.load(stream)
        assert not stream.closed
