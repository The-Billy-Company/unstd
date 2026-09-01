"""The benchmark harness behind the speedups the READMEs claim.

``unstd`` exists because a native backend is faster than the stdlib module it
stands in for. That is a measurable claim, and until this package landed it was
an unmeasured one — the clone README carried a table of microseconds annotated
"measured on this dev env" with no way for anyone, including its author, to run
it again.

Usage::

    python3 -m bench                 # measure everything, print a table
    python3 -m bench --group serde   # one group
    python3 -m bench verify          # exit non-zero if a claim fell through
    python3 -m bench update          # raise floors that genuinely improved
    python3 -m bench --markdown      # the table shape a README wants

See ``bench/README.md`` for what the numbers mean and why the baseline pins
floors rather than values.
"""

from __future__ import annotations

from bench.cases import Case, audit, groups, registry
from bench.report import Result, markdown, run, table, write_baseline
from bench.timing import Measurement, measure


__all__ = [
    "Case",
    "Measurement",
    "Result",
    "audit",
    "groups",
    "markdown",
    "measure",
    "registry",
    "run",
    "table",
    "write_baseline",
]
