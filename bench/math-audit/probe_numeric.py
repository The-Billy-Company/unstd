"""Compatibility script for ``bench.audits.math.probe_numeric``."""

from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    runpy.run_module("bench.audits.math.probe_numeric", run_name="__main__")
