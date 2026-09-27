"""Fast structural deep clone — a stdlib-``copy``-faithful stand-in for data.

``copy.deepcopy`` is a general object-graph walker and a well-known hot-path
sink. :func:`deep` clones the data that crosses a program's seams —
``dict``/``list``/``tuple``/``set`` trees, dataclasses, pydantic models,
``msgspec.Struct`` — with an exact-type walk that never pays ``deepcopy``'s memo
and reducer machinery, and hands every node it does not own to ``copy.deepcopy``
itself. :func:`asdict` is the same walker in ``dataclasses.asdict``'s shape. The
faithful :func:`copy`/:func:`deepcopy` re-exports keep a file migrating off
``import copy`` working after a one-line import swap.

Pure stdlib — no extra is needed::

    from unstd import clone

    twin = clone.deep(payload)  # structural fast clone
    plain = clone.asdict(record)  # dataclasses.asdict, walked
    shallow = clone.copy(payload)  # stdlib copy.copy
    exact = clone.deepcopy(payload)  # stdlib copy.deepcopy
"""

from __future__ import annotations

from unstd.clone.structural import asdict, copy, deep, deepcopy


__all__ = ["asdict", "copy", "deep", "deepcopy"]
