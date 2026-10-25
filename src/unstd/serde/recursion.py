"""Retain stdlib codec entry frames before exposing a native result.

Stdlib's Python encoder and decoder entry points add frames above their native
scanners. These two frames retain their recursion headroom without rewalking a
result. Callers retry a failed probe through stdlib for its own diagnostics.
"""

from __future__ import annotations


__all__ = ()


def _check_headroom[T](value: T) -> T:
    return _recursion_result(value)


def _recursion_result[T](value: T) -> T:
    return value
