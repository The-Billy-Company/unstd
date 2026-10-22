"""Hostile parser workers retain enforced memory limits under native sanitizers."""

from types import SimpleNamespace

import pytest

from tests.support.parsers import worker


@pytest.mark.parametrize("sanitized", [False, True])
def test_parser_memory_budget_uses_the_active_runtime(monkeypatch, sanitized):
    calls = []
    resource = SimpleNamespace(
        RLIMIT_CPU="cpu",
        RLIMIT_AS="address_space",
        setrlimit=lambda kind, limit: calls.append((kind, limit)),
    )
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setitem(worker.sys.modules, "resource", resource)
    monkeypatch.delenv("UNSTD_NATIVE_SANITIZERS", raising=False)
    if sanitized:
        monkeypatch.setenv("UNSTD_NATIVE_SANITIZERS", "1")
        monkeypatch.setenv("LD_PRELOAD", "/fixture/libasan.so")
        monkeypatch.setenv("ASAN_OPTIONS", "abort_on_error=1:hard_rss_limit_mb=512")
    worker._limits()
    assert calls == [("cpu", (4, 4))] + (
        [] if sanitized else [("address_space", (512 * 1024**2, 512 * 1024**2))]
    )


@pytest.mark.parametrize("missing", ["LD_PRELOAD", "ASAN_OPTIONS"])
def test_sanitized_worker_refuses_missing_runtime_or_memory_enforcement(
    monkeypatch, missing
):
    resource = SimpleNamespace(RLIMIT_CPU="cpu", setrlimit=lambda *_: None)
    monkeypatch.setattr(worker.sys, "platform", "linux")
    monkeypatch.setitem(worker.sys.modules, "resource", resource)
    monkeypatch.setenv("UNSTD_NATIVE_SANITIZERS", "1")
    monkeypatch.setenv("LD_PRELOAD", "/fixture/libasan.so")
    monkeypatch.setenv("ASAN_OPTIONS", "abort_on_error=1:hard_rss_limit_mb=512")
    monkeypatch.delenv(missing)
    with pytest.raises(AssertionError, match="required"):
        worker._limits()
