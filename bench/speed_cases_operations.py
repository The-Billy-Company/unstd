"""Compatibility entry point for ``bench.workloads.speed_cases_operations``."""

from __future__ import annotations

import importlib
from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    runpy.run_module("bench.workloads.speed_cases_operations", run_name="__main__")
else:
    sys.modules[__name__] = importlib.import_module(
        "bench.workloads.speed_cases_operations"
    )
