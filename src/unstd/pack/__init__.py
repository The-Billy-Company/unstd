"""Binary packing — the stdlib ``struct`` surface plus bulk-array acceleration.

- ``binpack`` — a stdlib-``struct``-faithful surface (``pack`` / ``unpack`` /
  ``unpack_from`` / ``pack_into`` / ``calcsize`` / ``iter_unpack`` / ``Struct`` /
  ``error``, re-exported byte-identically) *plus* :func:`~unstd.pack.binpack.pack_array`
  / :func:`~unstd.pack.binpack.unpack_array` for homogeneous numeric arrays. The
  array path prefers NumPy (the ``pack`` extra — ``tobytes`` / zero-copy
  ``frombuffer``) and falls back to stdlib ``struct`` when it is absent.

Import the module, not its members::

    from unstd.pack import binpack
"""

from __future__ import annotations
