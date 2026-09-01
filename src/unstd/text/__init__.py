"""Text similarity/matching — one fuzzy-string surface for the whole program.

- ``fuzz`` — fuzzy string similarity, matching, and edit distance. A drop-in for
  ``difflib`` similarity backed by ``rapidfuzz`` (Max Bachmann) on the fast path.
  The ``text`` extra provides the C++/SIMD kernels; a stdlib ``difflib`` +
  pure-Python DP fallback keeps the base install functional.

Import the module, not its members::

    from unstd.text import fuzz
"""

from __future__ import annotations
