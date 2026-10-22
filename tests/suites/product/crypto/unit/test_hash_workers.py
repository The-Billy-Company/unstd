"""Fresh BLAKE3 pools scale with the message without exceeding the CPU cap."""

from __future__ import annotations

import importlib

import pytest


blake3 = pytest.importorskip("blake3").blake3
backend = importlib.import_module("unstd.crypto._blake3")
digest = importlib.import_module("unstd.crypto.digest")
token = importlib.import_module("unstd.crypto.token")
_KEY = bytes(range(32))
_CONTEXT = "unstd adaptive hashing contract v1"


@pytest.mark.parametrize(
    ("length", "workers"),
    [
        (512 * 1024 - 1, None),
        (512 * 1024, None),
        (512 * 1024 + 1, None),
        (1024 * 1024 - 1, None),
        (1024 * 1024, 4),
        (1024 * 1024 + 1, 4),
        (4 * 1024 * 1024 - 1, 4),
        (4 * 1024 * 1024, 8),
        (4 * 1024 * 1024 + 1, 8),
    ],
)
def test_dispatch_boundaries_preserve_all_hashing_modes(monkeypatch, length, workers):
    calls = []

    def construct(data, *, max_threads=None, **options):
        calls.append((max_threads, options))
        return blake3(data, **options)

    monkeypatch.setattr(backend, "_PARALLEL_THREADS", 8)
    monkeypatch.setattr(backend, "blake3", construct)
    monkeypatch.setattr(digest, "blake3", construct)
    monkeypatch.setattr(token, "blake3", construct)
    data = bytes(length)
    expected = blake3(data).digest()
    keyed = blake3(data, key=_KEY).digest()
    derived = blake3(data, derive_key_context=_CONTEXT).digest()
    assert digest.raw(data) == expected
    assert digest.hex(data) == expected.hex()
    assert digest.derive_key(_CONTEXT, data) == derived
    assert token.mac_raw(_KEY, data) == keyed
    assert token.mac_hex(_KEY, data) == keyed.hex()
    assert calls == [
        (workers, {}),
        (workers, {}),
        (workers, {"derive_key_context": _CONTEXT}),
        (workers, {"key": _KEY}),
        (workers, {"key": _KEY}),
    ]


@pytest.mark.parametrize("cpu_cap", [1, 2, 3, 4, 8])
@pytest.mark.parametrize(
    ("length", "desired_workers"),
    [(1024 * 1024, 4), (2 * 1024 * 1024, 4), (4 * 1024 * 1024, 8)],
)
def test_adaptive_pools_obey_the_cpu_cap(monkeypatch, cpu_cap, length, desired_workers):
    calls = []
    result = object()

    def construct(data, *, max_threads):
        calls.append(max_threads)
        return result

    monkeypatch.setattr(backend, "_PARALLEL_THREADS", cpu_cap)
    monkeypatch.setattr(backend, "blake3", construct)
    assert backend._parallel_hasher(bytes(length)) is result
    assert calls == [min(cpu_cap, desired_workers)]


@pytest.mark.parametrize("length", [1024 * 1024, 2 * 1024 * 1024])
def test_smaller_pools_retry_without_reusing_a_failed_hasher(monkeypatch, length):
    panic = type("PanicException", (BaseException,), {"__module__": "pyo3_runtime"})
    calls = []

    def construct(data, *, max_threads=None, **options):
        calls.append(max_threads)
        if max_threads is not None:
            message = "ThreadPoolBuildError"
            raise panic(message)
        return blake3(data, **options)

    monkeypatch.setattr(backend, "_PARALLEL_THREADS", 8)
    monkeypatch.setattr(backend, "blake3", construct)
    data = bytes(length)
    assert (
        backend._parallel_hasher(data, key=_KEY).digest()
        == blake3(data, key=_KEY).digest()
    )
    assert calls == [4, None]
