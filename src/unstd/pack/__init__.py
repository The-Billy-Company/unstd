"""Binary packing — the stdlib ``struct`` surface plus bulk-array acceleration.

- ``binpack`` — a stdlib-``struct``-faithful surface (``pack`` / ``unpack`` /
  ``unpack_from`` / ``pack_into`` / ``calcsize`` / ``iter_unpack`` / ``Struct`` /
  ``error``, re-exported byte-identically) *plus* :func:`~unstd.pack.binpack.pack_array`
  / :func:`~unstd.pack.binpack.unpack_array` for homogeneous numeric arrays. The
  array path prefers NumPy (the ``pack`` extra — ``tobytes`` / zero-copy
  ``frombuffer``) and falls back to stdlib ``struct`` when it is absent.
- ``ragged`` — numeric boundary-table/matrix layouts with complete validation
  and immutable NumPy views or tuple results. Its optional native validator
  fuses geometry, boundary, and finite-value checks.

Import the module, not its members::

    from unstd.pack import binpack
"""

from __future__ import annotations
