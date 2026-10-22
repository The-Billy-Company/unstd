"""A missing ``model`` extra raises the documented ImportError, not a bare lookup.

Workers block one backend with a ``None`` module entry, the import system's
documented way to make ``import`` fail, so the installed dependencies stay
untouched and every case starts from a clean interpreter.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.process import isolated_environment, run_python


if TYPE_CHECKING:
    from pathlib import Path


_WORKER = """
import importlib, json, sys
for name in sys.argv[1].split(","):
    sys.modules[name] = None
outcomes = {}
for module in sys.argv[2].split(","):
    try:
        importlib.import_module(module)
    except ImportError as error:
        outcomes[module] = [type(error).__name__, str(error),
                            isinstance(error.__cause__, ImportError)]
    else:
        outcomes[module] = "imported"
print(json.dumps(outcomes))
"""

_HINT = "install with: pip install 'unstd[model]'"


def _imports(blocked: str, modules: str, tmp_path: Path) -> dict[str, object]:
    result = run_python(
        _WORKER,
        [blocked, modules],
        cwd=tmp_path,
        env=isolated_environment(),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("blocked", ["pydantic", "pydantic,pydantic_settings"])
def test_missing_pydantic_names_the_extra_from_every_entry_point(
    blocked: str, tmp_path: Path
) -> None:
    out = _imports(blocked, "unstd.model.base,unstd.model", tmp_path)
    expected = [
        "ImportError",
        f"unstd.model requires the 'model' extra — {_HINT}",
        True,
    ]
    assert out == {"unstd.model.base": expected, "unstd.model": expected}


def test_missing_pydantic_settings_fails_the_whole_group_with_the_hint(
    tmp_path: Path,
) -> None:
    # Importing a submodule executes the package __init__, which imports settings.
    out = _imports(
        "pydantic_settings", "unstd.model.base,unstd.model.settings", tmp_path
    )
    expected = [
        "ImportError",
        f"unstd.model.settings requires the 'model' extra — {_HINT}",
        True,
    ]
    assert out == {"unstd.model.base": expected, "unstd.model.settings": expected}
