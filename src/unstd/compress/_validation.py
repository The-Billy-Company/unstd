"""Shared C-integer validation without moving codec callback boundaries."""

from operator import index
from struct import calcsize
from typing import cast


_BITS = calcsize("i") * 8
_MINIMUM = -(1 << (_BITS - 1))
_MAXIMUM = (1 << (_BITS - 1)) - 1


def flush_mode(value: int) -> int:
    """Coerce once before native truncation could finish a live stream."""
    mode = index(value)
    if not _MINIMUM <= mode <= _MAXIMUM:
        message = "Python int too large to convert to C int"
        raise OverflowError(message)
    return mode


class _DeferredMode:
    """Validate where the provider normally coerces its flush argument."""

    __slots__ = ("_value",)

    def __init__(self, value: int) -> None:
        """Retain the original argument until the codec asks for its index."""
        self._value = value

    def __index__(self) -> int:
        """The checked native integer, at the original callback boundary."""
        return flush_mode(self._value)


def deferred_flush_mode(value: int) -> int:
    """Keep ordinary modes direct; defer unusual values to the codec call."""
    if type(value) is int and _MINIMUM <= value <= _MAXIMUM:
        return value
    return cast("int", _DeferredMode(value))
