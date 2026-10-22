"""Interleaved readers and writers never share anchors, options or emitter state.

YAML 1.2 §9.2 makes every document's anchors private. Expected documents are
written out by hand; exact types distinguish safe values from editable wrappers.
"""

from __future__ import annotations

import io
from threading import Barrier, Thread
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from unstd import yaml


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


def test_interleaved_streams_keep_private_anchors():
    left = yaml.load_all(
        "---\nbase: &a [1]\ncopy: *a\n---\nonly: &left x\nref: *left\n"
    )
    right = yaml.load_all("---\nbase: &a [10]\ncopy: *a\n---\nref: *left\n")
    first_left, first_right = next(left), next(right)
    assert first_left == {"base": [1], "copy": [1]}
    assert first_right == {"base": [10], "copy": [10]}
    assert first_left["base"] is first_left["copy"]
    assert first_right["base"] is first_right["copy"]
    assert first_left["base"] is not first_right["base"]
    assert next(left) == {"only": "x", "ref": "x"}
    # The right stream's alias names an anchor that exists only in the left one.
    with pytest.raises(yaml.YAMLError, match="undefined alias"):
        next(right)
    assert list(left) == []


def test_anchors_never_cross_a_document_boundary():
    documents = yaml.load_all("---\na: &x [1]\n---\nb: *x\n")
    assert next(documents) == {"a": [1]}
    with pytest.raises(yaml.YAMLError, match="undefined alias"):
        next(documents)


def test_editable_options_do_not_leak_into_safe_reads():
    editable = yaml.parse("label: 'on'\nport: 0x1F\n")
    native = yaml.loads("label: 'on'\nport: 0x1F\n")
    assert type(native["label"]) is str
    assert type(native["port"]) is int
    assert type(native) is dict
    assert type(editable["label"]) is not str
    assert isinstance(editable["label"], str)
    assert yaml.dumps(editable) == "label: 'on'\nport: 0x1F\n"
    assert yaml.dumps(native) == "label: on\nport: 31\n"
    assert yaml.dumps(yaml.parse("label: 'on'\n")) == "label: 'on'\n"


def test_safe_failures_mid_stream_do_not_disturb_an_open_editable_parse():
    documents = yaml.load_all("---\nfirst: 1\n---\nkey: !unknown value\n")
    assert next(documents) == {"first": 1}
    editable = yaml.parse('# kept\nname: "quoted"\n')
    with pytest.raises(yaml.YAMLError):
        next(documents)
    assert yaml.dumps(editable) == '# kept\nname: "quoted"\n'


def test_writers_paused_mid_stream_are_independent():
    left, right = io.StringIO(), io.StringIO()

    def documents(stream: io.StringIO, label: str) -> Iterator[object]:
        yield {"stream": label}
        # Re-enter the module with another writer and both readers mid-dump.
        if label == "left":
            yaml.dump_all([{"stream": "right"}, ["nested"]], right)
            assert yaml.loads(stream.getvalue()) == {"stream": "left"}
            assert yaml.parse("x: 'y'\n") == {"x": "y"}
        yield [label, 1]

    yaml.dump_all(documents(left, "left"), left)
    assert left.getvalue() == "stream: left\n---\n- left\n- 1\n"
    assert right.getvalue() == "stream: right\n---\n- nested\n"
    assert list(yaml.load_all(left.getvalue())) == [{"stream": "left"}, ["left", 1]]


def test_threads_with_paused_writers_and_iterators_stay_isolated():
    parties = 6
    barrier = Barrier(parties, timeout=10)
    results: dict[int, tuple[str, list[object]]] = {}
    failures: list[BaseException] = []

    def worker(index: int) -> None:
        try:
            stream = io.StringIO()
            reads = yaml.load_all(
                f"---\nv: &a [{index}]\nw: *a\n---\nlabel: '{index}'\n"
            )
            first = next(reads)

            def documents() -> Iterator[object]:
                yield {"index": index}
                barrier.wait()
                yield yaml.parse(f"quoted: '{index}'\n")

            yaml.dump_all(documents(), stream)
            barrier.wait()
            results[index] = (stream.getvalue(), [first, *reads])
        except BaseException as error:  # surfaced in the parent thread below
            failures.append(error)
            barrier.abort()

    threads = [Thread(target=worker, args=(i,)) for i in range(parties)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not failures, failures
    assert not any(thread.is_alive() for thread in threads)
    for index in range(parties):
        text, read = results[index]
        assert text == f"index: {index}\n---\nquoted: '{index}'\n"
        assert read == [{"v": [index], "w": [index]}, {"label": str(index)}]
        assert read[0]["v"] is read[0]["w"]
