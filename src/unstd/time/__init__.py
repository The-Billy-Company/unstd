"""Clocks and time — absolute instants in the stdlib, wall-clock zones in ``whenever``.

- ``timeutil`` — wall / monotonic / perf clocks, RFC-3339 formatting, ISO
  parsing, epoch conversion, and instant arithmetic (``span``). Pure stdlib; the
  protobuf ``Timestamp`` bridge lazy-imports ``google.protobuf`` (``proto`` extra).
- ``zoned`` — DST-correct wall-clock times in an IANA zone: resolve a local time,
  localize a parsed stamp, pick a side of a fold or gap. Requires the ``time``
  extra (``whenever``).

Import the module, not its members::

    from unstd.time import timeutil, zoned
"""

from __future__ import annotations
