"""Explicit scenario environments retain fallback and outer group ownership."""

import json
import os

from tests.support.process import isolated_environment, run_python


def test_explicit_worker_environment_cannot_discard_campaign_admission(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("UNSTD_CHAOS_BACKEND", "stdlib")
    monkeypatch.setenv("UNSTD_CHAOS_OWNED_GROUP", "1")
    code = """
import json
import os
try:
    import numpy
except ModuleNotFoundError as error:
    blocked = 'stdlib chaos worker blocks numpy' in str(error)
else:
    blocked = False
print(json.dumps({
    'blocked': blocked,
    'backend': os.environ.get('UNSTD_CHAOS_BACKEND'),
    'owned': os.environ.get('UNSTD_CHAOS_OWNED_GROUP'),
    'explicit': os.environ.get('EXPLICIT_SCENARIO_VALUE'),
    'same_group': os.getpgrp() == os.getpgid(os.getppid()) if os.name == 'posix' else None,
}))
"""
    result = run_python(
        code,
        cwd=tmp_path,
        env=isolated_environment(EXPLICIT_SCENARIO_VALUE="retained"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "blocked": True,
        "backend": "stdlib",
        "owned": "1",
        "explicit": "retained",
        "same_group": True if os.name == "posix" else None,
    }
