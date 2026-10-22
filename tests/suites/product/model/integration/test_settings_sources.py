"""Environment and dotenv precedence for ``StrictSettings`` in isolated interpreters.

Each scenario runs a fresh worker whose environment holds only fixture-owned
variables, with a fixture-written dotenv file in its own working directory, so
the developer's environment can never reach a result. Expected values follow
the pydantic-settings source order (initializer, environment, dotenv, default);
an explicitly configured ``BaseSettings`` twin runs beside every scenario, and a
default-``forbid`` control shows what the root ``ignore`` policy prevents.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.support.environment.process import isolated_environment, run_python


if TYPE_CHECKING:
    from pathlib import Path


_WORKER = """
import json, sys
from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from unstd.model import StrictModel, StrictSettings

class Group(StrictModel):
    host: str = "default-host"
    port: int = 1

SOURCES = dict(env_prefix="SVC_", env_file=".env", env_nested_delimiter="__")
FIELDS = {
    "name": (str, "default"),
    "level": (int, 0),
    "token": (str, Field(default="none", validation_alias="SERVICE_TOKEN")),
    "group": (Group, Group()),
}

def build(base, **config):
    namespace = {"model_config": SettingsConfigDict(**config), "__annotations__": {}}
    for field, (kind, default) in FIELDS.items():
        namespace["__annotations__"][field] = kind
        namespace[field] = default
    return type(base.__name__ + "Shape", (base,), namespace)

classes = {
    "ours": build(StrictSettings, **SOURCES),
    "twin": build(BaseSettings, extra="ignore", validate_by_name=True, **SOURCES),
    "forbid": build(BaseSettings, validate_by_name=True, **SOURCES),
    "bare": build(StrictSettings, env_prefix="SVC_", env_nested_delimiter="__"),
}
init = json.loads(sys.argv[1])
out = {}
for label, cls in classes.items():
    try:
        out[label] = ["ok", cls(**init).model_dump()]
    except ValidationError as error:
        out[label] = ["error", sorted([e["type"], list(e["loc"])] for e in error.errors())]
print(json.dumps(out))
"""

_DEFAULTS = {
    "name": "default",
    "level": 0,
    "token": "none",
    "group": {"host": "default-host", "port": 1},
}


def _run(
    tmp_path: Path,
    *,
    env: dict[str, str],
    dotenv: str | None = None,
    init: dict[str, Any] | None = None,
) -> dict[str, list[Any]]:
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
    result = run_python(
        _WORKER,
        [json.dumps(init or {})],
        cwd=tmp_path,
        env=isolated_environment(**env),
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["ours"] == out["twin"], "explicit-config twin disagrees"
    return out


def _ok(**changes: Any) -> list[Any]:
    return ["ok", {**_DEFAULTS, **changes}]


_SCENARIOS = {
    "defaults": ({}, None, None, _ok()),
    "dotenv-only": ({}, "SVC_NAME=dot\nSVC_LEVEL=2\n", None, _ok(name="dot", level=2)),
    "environment-beats-dotenv": (
        {"SVC_NAME": "env"},
        "SVC_NAME=dot\nSVC_LEVEL=3\n",
        None,
        _ok(name="env", level=3),
    ),
    "initializer-beats-environment-and-dotenv": (
        {"SVC_NAME": "env", "SVC_LEVEL": "4"},
        "SVC_NAME=dot\n",
        {"name": "init"},
        _ok(name="init", level=4),
    ),
    "case-insensitive-environment": ({"svc_level": "5"}, None, None, _ok(level=5)),
    "alias-ignores-prefix": (
        {"SERVICE_TOKEN": "env-token", "SVC_TOKEN": "prefixed"},
        None,
        None,
        _ok(token="env-token"),  # noqa: S106 — synthetic settings input.
    ),
    "initializer-by-name": (
        {"SERVICE_TOKEN": "env-token"},
        None,
        {"token": "by-name"},
        _ok(token="by-name"),  # noqa: S106 — synthetic settings input.
    ),
    "initializer-by-alias": (
        {"SERVICE_TOKEN": "env-token"},
        "SERVICE_TOKEN=dot-token\n",
        {"SERVICE_TOKEN": "by-alias"},
        _ok(token="by-alias"),  # noqa: S106 — synthetic settings input.
    ),
    "nested-groups-merge-sources": (
        {"SVC_GROUP__HOST": "env-host"},
        "SVC_GROUP__PORT=9\n",
        None,
        _ok(group={"host": "env-host", "port": 9}),
    ),
}


@pytest.mark.parametrize(
    ("env", "dotenv", "init", "expected"),
    list(_SCENARIOS.values()),
    ids=list(_SCENARIOS),
)
def test_source_precedence(
    tmp_path: Path,
    env: dict[str, str],
    dotenv: str | None,
    init: dict[str, Any] | None,
    expected: list[Any],
) -> None:
    assert _run(tmp_path, env=env, dotenv=dotenv, init=init)["ours"] == expected


def test_root_ignores_ambient_and_dotenv_extras_that_default_settings_reject(
    tmp_path: Path,
) -> None:
    out = _run(
        tmp_path,
        env={"UNRELATED_AMBIENT": "1", "SVC_UNDECLARED": "2"},
        dotenv="SVC_NAME=dot\nSVC_BOGUS=1\nOTHER_KEY=2\n",
        init={"not_a_field": 3},
    )
    assert out["ours"] == _ok(name="dot")
    assert out["forbid"] == [
        "error",
        [
            ["extra_forbidden", ["not_a_field"]],
            ["extra_forbidden", ["other_key"]],
            ["extra_forbidden", ["svc_bogus"]],
        ],
    ]


def test_nested_strict_group_rejects_unknown_keys_from_every_source(
    tmp_path: Path,
) -> None:
    out = _run(
        tmp_path,
        env={"SVC_GROUP__BOGUS": "1"},
        dotenv="SVC_GROUP__EXTRA=2\n",
    )
    assert out["ours"] == [
        "error",
        [
            ["extra_forbidden", ["group", "bogus"]],
            ["extra_forbidden", ["group", "extra"]],
        ],
    ]


def test_base_configures_no_dotenv_file(tmp_path: Path) -> None:
    out = _run(tmp_path, env={"SVC_LEVEL": "6"}, dotenv="SVC_NAME=dot\n")
    assert out["bare"] == _ok(level=6)
    assert out["ours"] == _ok(name="dot", level=6)


def test_invalid_environment_value_reports_the_field(tmp_path: Path) -> None:
    out = _run(tmp_path, env={"SVC_LEVEL": "not-a-number"})
    assert out["ours"] == ["error", [["int_parsing", ["level"]]]]
