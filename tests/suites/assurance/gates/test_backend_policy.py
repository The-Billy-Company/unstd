"""Required CI capabilities cannot silently become optional import skips."""

from __future__ import annotations

import pytest

from tests.support import backends


def test_required_missing_backend_is_a_failure_with_success_control(monkeypatch):
    monkeypatch.setattr(backends, "_REQUIRED", True)
    assert backends.require_backend("json").loads('{"ok":true}') == {"ok": True}
    with pytest.raises(ModuleNotFoundError, match="unstd_nonexistent_backend_fixture"):
        backends.require_backend("unstd_nonexistent_backend_fixture")


def test_optional_local_missing_backend_is_an_explicit_capability_skip(monkeypatch):
    monkeypatch.setattr(backends, "_REQUIRED", False)
    assert backends.require_backend("json").loads("[1]") == [1]
    with pytest.raises(pytest.skip.Exception, match="optional local backend"):
        backends.require_backend("unstd_nonexistent_backend_fixture")
