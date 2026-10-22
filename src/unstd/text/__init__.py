"""Text similarity/matching — one fuzzy-string surface for the whole program.

- ``fuzz`` — fuzzy string similarity, matching, and edit distance. Explicit
  ``indel_ratio`` and ``difflib_ratio`` scorers keep their algorithm across
  installations. The historical ``ratio`` selects RapidFuzz Indel with the
  ``text`` extra and difflib otherwise, so its scores and rankings can differ.
  Portable scoring bodies keep the base install functional.

Import the module, not its members::

    from unstd.text import fuzz
"""

from __future__ import annotations
