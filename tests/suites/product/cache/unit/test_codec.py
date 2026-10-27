"""Typed binary fills validate both paths without weakening JSON cache behavior."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import struct
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import run_python
from unstd.cache import Cache


if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class _Artifact:
    tokens: list[int]


class _Codec:
    def encode(self, value: _Artifact, /) -> bytes:
        return b"TOK1" + struct.pack(
            f">I{len(value.tokens)}i", len(value.tokens), *value.tokens
        )

    def decode(self, raw: bytes, /) -> _Artifact:
        if len(raw) < 8 or raw[:4] != b"TOK1":
            message = "invalid token artifact header"
            raise ValueError(message)
        count = struct.unpack_from(">I", raw, 4)[0]
        if len(raw) != 8 + count * 4:
            message = "invalid token artifact length"
            raise ValueError(message)
        return _Artifact(list(struct.unpack_from(f">{count}i", raw, 8)))


@dataclass
class _CallableCodec:
    encode: Callable[[_Artifact], bytes]
    decode: Callable[[bytes], _Artifact]


@pytest.mark.parametrize("callable_attributes", [False, True])
def test_method_and_callable_codecs_return_independent_typed_values(
    tmp_path, callable_attributes
):
    methods = _Codec()
    codec = (
        _CallableCodec(methods.encode, methods.decode)
        if callable_attributes
        else methods
    )
    cache = Cache(tmp_path / "cache.sqlite3", namespace="tokens-v1")
    source = _Artifact([1, 2, -100])
    calls = []

    def produce():
        calls.append("produced")
        return source

    cold = cache.get_or_compute("row", produce, codec=codec)
    assert type(cold) is _Artifact
    assert cold == source
    assert cold is not source
    source.tokens.append(4)
    cold.tokens.append(5)
    warm = cache.get_or_compute("row", produce, codec=codec)
    assert warm == _Artifact([1, 2, -100])
    assert calls == ["produced"]
    with sqlite3.connect(cache._path) as database:
        kind, payload = database.execute(
            "SELECT typeof(value), value FROM _unstd_cache_v1"
        ).fetchone()
    assert kind == "blob"
    assert payload == methods.encode(_Artifact([1, 2, -100]))


def test_reopened_codec_artifact_is_reused_in_another_interpreter(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path, namespace="tokens-v1")
    cache.get_or_compute("row", lambda: _Artifact([1, -100]), codec=_Codec())
    result = run_python(
        """
import sys
from tests.suites.product.cache.unit.test_codec import _Artifact, _Codec
from unstd.cache import Cache
def unexpected():
    raise AssertionError('persisted artifact recomputed')
value = Cache(sys.argv[1], namespace='tokens-v1').get_or_compute(
    'row', unexpected, codec=_Codec()
)
assert type(value) is _Artifact
assert value.tokens == [1, -100]
print('CODEC_OK')
""",
        [str(path)],
        cwd=tmp_path,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "CODEC_OK\n"


def test_cold_decode_rejection_publishes_nothing_and_releases_fill_lock(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3", namespace="tokens-v1", timeout=0)
    methods = _Codec()

    def reject(raw):
        message = "incompatible artifact"
        raise ValueError(message)

    rejecting = _CallableCodec(methods.encode, reject)
    with pytest.raises(ValueError, match="incompatible artifact"):
        cache.get_or_compute("row", lambda: _Artifact([1]), codec=rejecting)
    assert cache.get("row", "absent") == "absent"
    assert cache.get_or_compute(
        "row", lambda: _Artifact([2]), codec=methods
    ) == _Artifact([2])


def test_encoding_error_publishes_nothing_and_preserves_other_entries(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3", namespace="tokens-v1", timeout=0)
    cache.set("other", {"retained": True})

    def reject(value):
        message = "encoding failed"
        raise RuntimeError(message)

    codec = _CallableCodec(reject, _Codec().decode)
    with pytest.raises(RuntimeError, match="encoding failed"):
        cache.get_or_compute("row", lambda: _Artifact([1]), codec=codec)
    assert cache.get("row", "absent") == "absent"
    assert cache.get("other") == {"retained": True}


@pytest.mark.parametrize("encoded", ["text", bytearray(b"TOK1"), memoryview(b"TOK1")])
def test_codec_encoder_must_return_bytes(tmp_path, encoded):
    cache = Cache(tmp_path / "cache.sqlite3", namespace="tokens-v1")
    codec = SimpleNamespace(
        encode=lambda value: encoded,
        decode=lambda raw: pytest.fail("invalid encoded type reached decoder"),
    )
    with pytest.raises(TypeError, match="encode must return bytes"):
        cache.get_or_compute("row", lambda: _Artifact([1]), codec=codec)
    assert cache.get("row", "absent") == "absent"


def test_corrupt_warm_artifact_raises_without_running_factory_or_deleting_it(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path, namespace="tokens-v1")
    codec = _Codec()
    cache.get_or_compute("row", lambda: _Artifact([1]), codec=codec)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE _unstd_cache_v1 SET value = ?", (b"corrupt",))
    with pytest.raises(ValueError, match="invalid token artifact header"):
        cache.get_or_compute(
            "row", lambda: pytest.fail("corrupt artifact recomputed"), codec=codec
        )
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT value FROM _unstd_cache_v1").fetchone() == (
            b"corrupt",
        )


def test_nonbinary_stored_artifact_is_rejected_before_typed_decode(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = Cache(path, namespace="tokens-v1")
    cache.get_or_compute("row", lambda: _Artifact([1]), codec=_Codec())
    with sqlite3.connect(path) as database:
        database.execute("UPDATE _unstd_cache_v1 SET value = ?", ("text",))
    codec = _CallableCodec(
        _Codec().encode,
        lambda raw: pytest.fail("nonbinary artifact reached typed decoder"),
    )
    with pytest.raises(TypeError, match="artifact is not bytes"):
        cache.get_or_compute(
            "row", lambda: pytest.fail("invalid artifact recomputed"), codec=codec
        )


def test_typed_codec_observes_expiry_after_the_factory_completes(tmp_path, monkeypatch):
    import time

    clock = [1_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    cache = Cache(tmp_path / "cache.sqlite3", namespace="tokens-v1")
    calls = []

    def produce():
        calls.append("produced")
        clock[0] += 10
        return _Artifact([len(calls)])

    assert cache.get_or_compute("row", produce, ttl=1, codec=_Codec()) == _Artifact([1])
    assert cache.get_or_compute("row", produce, ttl=1, codec=_Codec()) == _Artifact([1])
    clock[0] += 1
    assert cache.get_or_compute("row", produce, ttl=1, codec=_Codec()) == _Artifact([2])
    assert calls == ["produced", "produced"]


def test_default_json_remains_independent_from_versioned_binary_artifacts(tmp_path):
    path = tmp_path / "cache.sqlite3"
    binary = Cache(path, namespace="tokens-v1")
    default = Cache(path)
    binary.get_or_compute("row", lambda: _Artifact([1]), codec=_Codec())
    assert default.get_or_compute("row", lambda: {"tokens": [2]}) == {"tokens": [2]}
    assert binary.get_or_compute(
        "row", lambda: pytest.fail("binary artifact recomputed"), codec=_Codec()
    ) == _Artifact([1])


def test_noncallable_codec_is_rejected_before_running_factory(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite3")
    codec = SimpleNamespace(encode=None, decode=None)
    with pytest.raises(TypeError, match="codec must provide callable"):
        cache.get_or_compute(
            "row", lambda: pytest.fail("invalid codec ran producer"), codec=codec
        )
