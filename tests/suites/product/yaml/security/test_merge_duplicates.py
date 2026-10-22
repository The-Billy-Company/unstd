"""YAML merges must not disable duplicate explicit-key validation."""

from __future__ import annotations

import io

import pytest

from tests.support.environment.backends import require_backend
from unstd import yaml


@pytest.fixture(autouse=True)
def yaml_backend():
    require_backend("ruamel.yaml")


def _read(operation, source):
    if operation == "load":
        return yaml.load(io.StringIO(source))
    if operation == "load_all":
        return list(yaml.load_all(source))
    return getattr(yaml, operation)(source)


@pytest.mark.parametrize("operation", ["loads", "load", "load_all", "parse"])
@pytest.mark.parametrize(
    "source",
    [
        "defaults: &defaults {role: reader}\nuser: {<<: *defaults, role: reader, role: admin}\n",
        "defaults: &defaults {role: reader}\nuser: {<<: *defaults, true: reader, 1: admin}\n",
        "user: {<<: {role: reader, role: admin}}\n",
    ],
    ids=["merged-target", "equal-typed-keys", "inline-merge-source"],
)
def test_merges_cannot_hide_duplicate_explicit_keys(operation, source):
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        _read(operation, source)


@pytest.mark.parametrize("operation", ["loads", "load", "load_all", "parse"])
def test_merge_precedence_and_single_explicit_overrides_remain_valid(operation):
    # Reusing an anchor with its own override must not validate flattened
    # inherited keys as if they had been written twice in the source document.
    source = (
        "base: &base {role: reader, enabled: true}\n"
        "defaults: &defaults {<<: *base, role: writer}\n"
        "user: {<<: [*defaults, *base], role: admin}\n"
    )
    value = _read(operation, source)
    if operation == "load_all":
        assert len(value) == 1
        value = value[0]
    assert value["defaults"] == {"role": "writer", "enabled": True}
    assert value["user"] == {"role": "admin", "enabled": True}
