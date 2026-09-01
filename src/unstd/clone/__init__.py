"""Fast structural deep clone — a stdlib-``copy``-faithful accelerated stand-in.

``copy.deepcopy`` is a pure-Python object-graph walker and a well-known hot-path
sink; for wire-shaped data (``dict``/``list``/``tuple``/``set``/``dataclass``/
``msgspec.Struct``) a structural round-trip through ``msgspec.json`` (encode to
bytes, then decode back to the original type) is far faster and still fully
independent — *not* ``msgspec.convert``, which reuses already-correct nested
objects and so shares inner containers with the source (see
:mod:`unstd.clone.structural` for why that would be a shallow copy in disguise).
:func:`deep` takes that fast path when it can and falls back to
``copy.deepcopy`` for everything else, and the faithful :func:`copy`/
:func:`deepcopy` re-exports keep a file migrating off ``import copy`` working
after a one-line import swap.

Backend & fallback: the fast path rides the ``clone`` extra
(``pip install 'unstd[clone]'``, providing ``msgspec``). Without it — a base
``unstd`` install — every call transparently falls back to ``copy.deepcopy``.
Import the module::

    from unstd import clone

    twin = clone.deep(payload)  # structural fast clone (deepcopy fallback)
    shallow = clone.copy(payload)  # stdlib copy.copy
    exact = clone.deepcopy(payload)  # stdlib copy.deepcopy
"""

from __future__ import annotations

from unstd.clone.structural import copy, deep, deepcopy


__all__ = ["copy", "deep", "deepcopy"]
