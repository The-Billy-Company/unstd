"""Clock, monotonic, perf-counter, and date arithmetic helpers.

- ``timeutil`` — wall (``utcnow``/``wall``), monotonic (``mono``), perf
  (``perf``/``elapsed_ms``), ISO + locale formatting. Pure stdlib; the protobuf
  ``Timestamp`` bridge lazy-imports ``google.protobuf`` on call.
- ``dateutil`` — the typed-instant seam (``whenever``): ISO parsing, DST-correct
  absolute-instant math, ranges, lookback windows. Requires the ``time`` extra.

Import the module, not its members::

    from unstd.time import timeutil, dateutil
"""

from __future__ import annotations
