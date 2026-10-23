"""Compatibility script for ``bench.audits.math.qualify_audio``."""

from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    runpy.run_module("bench.audits.math.qualify_audio", run_name="__main__")
