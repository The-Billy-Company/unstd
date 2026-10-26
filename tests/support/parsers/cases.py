"""Parent-controlled hostile-input execution with bounded data and lifetime."""

from __future__ import annotations

import base64
from importlib.util import find_spec
import json
import os
from pathlib import Path
import subprocess
import sys


def parser_case(target: str, raw: bytes, **options: str) -> dict[str, object]:
    """Run a parser in a fresh interpreter and reject crashes or missing evidence."""
    assert len(raw) <= 1024 * 1024
    envelope = {"target": target, "raw": base64.b64encode(raw).decode(), **options}
    package = find_spec("unstd")
    assert package is not None
    assert package.origin is not None
    origin = Path(package.origin).resolve()
    source = Path(__file__).resolve().parents[3] / "src/unstd"
    envelope["source"] = origin.is_relative_to(source)
    envelope["package_root"] = str(origin.parent.parent)
    worker = Path(__file__).with_name("worker.py")
    environment = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    if environment.get("UNSTD_NATIVE_SANITIZERS") == "1":
        # ASan reserves a huge virtual shadow map. Keep the resident-memory
        # budget enforced by its runtime instead of constraining that map.
        environment["ASAN_OPTIONS"] = (
            environment.get("ASAN_OPTIONS", "") + ":hard_rss_limit_mb=512"
        )
    completed = subprocess.run(  # noqa: S603 — fixed interpreter and reviewed worker
        [sys.executable, "-I", str(worker)],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    assert not completed.stderr, completed.stderr[-4000:]
    assert len(completed.stdout) <= 4096
    result = json.loads(completed.stdout)
    assert isinstance(result, dict)
    observed_origin = Path(result.pop("origin")).resolve()
    assert observed_origin == origin, (observed_origin, origin)
    assert result.get("kind") in {"value", "error"}
    return result
