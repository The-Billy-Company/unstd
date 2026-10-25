"""Sortedcontainers subclasses with private native positional kernels.

The SortedDict and SortedSet initializers adapt sortedcontainers 2.4.0 so their
internal list uses the same accelerated implementation as the public list.
Copyright 2014-2019 Grant Jenks. Licensed under Apache-2.0; see LICENSE.
All other behavior remains inherited from the optional upstream dependency.
"""

from __future__ import annotations

from inspect import signature
from typing import TYPE_CHECKING, Any, Self, cast

from sortedcontainers import SortedDict as _SortedDict
from sortedcontainers import SortedKeyList as _SortedKeyList
from sortedcontainers import SortedList as _SortedList
from sortedcontainers import SortedSet as _SortedSet

from unstd.containers import _sorted_native


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class SortedList(_SortedList[Any]):
    """The upstream sorted list with optional native positional operations."""

    def __new__(
        cls,
        iterable: Iterable[Any] | None = None,
        key: Callable[[Any], Any] | None = None,
    ) -> Self:
        """Choose the keyed implementation exactly as the upstream factory does."""
        if key is None:
            return object.__new__(cls)
        if cls is SortedList:
            return cast("Self", object.__new__(SortedKeyList))
        msg = "inherit SortedKeyList for key argument"
        raise TypeError(msg)


class SortedKeyList(_SortedKeyList[Any, Any], SortedList):
    """The upstream keyed list with the same accelerated positional operations."""


def _bind_list_queries(owner: Any, values: Any, key: Any) -> None:
    """Keep the upstream per-instance query bindings on the selected list."""
    owner.bisect_left = values.bisect_left
    owner.bisect = values.bisect_right
    owner.bisect_right = values.bisect_right
    owner.index = values.index
    owner.irange = values.irange
    owner.islice = values.islice
    owner._reset = values._reset
    if key is not None:
        owner.bisect_key_left = values.bisect_key_left
        owner.bisect_key_right = values.bisect_key_right
        owner.bisect_key = values.bisect_key
        owner.irange_key = values.irange_key


class SortedDict(_SortedDict[Any, Any]):
    """An upstream sorted dict whose key index uses the accelerated list."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with the upstream positional key and mapping semantics."""
        if args and (args[0] is None or callable(args[0])):
            key = self._key = args[0]
            args = args[1:]
        else:
            key = self._key = None

        values = self._list = SortedList(key=key)
        self._list_add = values.add
        self._list_clear = values.clear
        self._list_iter = values.__iter__
        self._list_reversed = values.__reversed__
        self._list_pop = values.pop
        self._list_remove = values.remove
        self._list_update = values.update
        _bind_list_queries(self, values, key)
        owner: Any = self
        owner._update(*args, **kwargs)


class SortedSet(_SortedSet[Any]):
    """An upstream sorted set whose ordered index uses the accelerated list."""

    def __init__(
        self,
        iterable: Iterable[Any] | None = None,
        key: Callable[[Any], Any] | None = None,
    ) -> None:
        """Initialize without replacing a set prepared by the upstream factory."""
        owner: Any = self
        owner._key = key
        if not hasattr(self, "_set"):
            owner._set = set()
        values = owner._list = SortedList(owner._set, key=key)
        owner.isdisjoint = owner._set.isdisjoint
        owner.issubset = owner._set.issubset
        owner.issuperset = owner._set.issuperset
        _bind_list_queries(self, values, key)
        if iterable is not None:
            owner._update(iterable)


# Public pickle paths remain importable even in an installation without native
# kernels: sorted.py binds each name to its original upstream class there.
for _type, _base in (
    (SortedList, _SortedList),
    (SortedKeyList, _SortedKeyList),
    (SortedDict, _SortedDict),
    (SortedSet, _SortedSet),
):
    _type.__module__ = "unstd.containers.sorted"
    _owner: Any = _type
    _owner.__signature__ = signature(_base)
    _owner.__unstd_backend__ = _base
del _type, _base

_sorted_native.install(SortedList, _SortedList)
_sorted_native.install(SortedKeyList, _SortedKeyList, keyed=True)
