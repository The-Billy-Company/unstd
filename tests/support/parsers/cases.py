"""Parent-controlled hostile-input execution with bounded data and lifetime."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys


def parser_case(target: str, raw: bytes, **options: str) -> dict[str, object]:
    """Run a parser in a fresh interpreter and reject crashes or missing evidence."""
    assert len(raw) <= 1024 * 1024
    envelope = {"target": target, "raw": base64.b64encode(raw).decode(), **options}
    worker = Path(__file__).with_name("worker.py")
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and reviewed worker
        [sys.executable, "-I", str(worker)],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    assert not completed.stderr, completed.stderr[-4000:]
    assert len(completed.stdout) <= 4096
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    origin = Path(result.pop("origin"))
    source = Path(__file__).resolve().parents[3] / "src/unstd"
    assert origin.is_relative_to(source), (origin, source)
    assert result.get("kind") in {"value", "error"}
    return result
