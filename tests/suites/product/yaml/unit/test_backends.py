"""The published backend nouns retain their actual modules and parser objects."""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

from tests.support.environment.backends import require_backend


def test_pyyaml_is_the_incumbent_module_without_callable_wrappers():
    require_backend("yaml")
    from unstd.yaml import pyyaml

    incumbent = importlib.import_module("yaml")
    assert pyyaml is incumbent
    assert pyyaml.safe_load is incumbent.safe_load
    assert pyyaml.safe_dump is incumbent.safe_dump
    assert pyyaml.SafeLoader is incumbent.SafeLoader
    assert pyyaml.MappingNode is incumbent.nodes.MappingNode
    assert pyyaml.events.ScalarEvent is incumbent.events.ScalarEvent
    if hasattr(incumbent, "CSafeLoader"):
        assert pyyaml.CSafeLoader is incumbent.CSafeLoader


@pytest.mark.parametrize(
    "source",
    [
        "on: yes\nnumber: 012\n",
        "duplicate: first\nduplicate: last\n",
        "base: &base {enabled: true}\nmerged: {<<: *base}\n",
        "cycle: &cycle [*cycle]\n",
        'unicode: "café 東京"\n',
        "value: a\tb\n",
        "value: a\ud800b\n",
        "value: [unfinished\n",
        'value: "\\invalid"\n',
        "value: *missing\n",
        "!!python/object/apply:builtins.str [unexpected]\n",
        "first: 1\n---\nsecond: 2\n",
    ],
)
def test_incumbent_values_and_diagnostics_are_identical(source):
    require_backend("yaml")
    from unstd.yaml import pyyaml

    incumbent = importlib.import_module("yaml")
    expected_error = None
    try:
        expected = incumbent.safe_load(source)
    except incumbent.YAMLError as error:
        expected_error = error
    if expected_error is not None:
        with pytest.raises(type(expected_error)) as actual:
            pyyaml.safe_load(source)
        assert str(actual.value) == str(expected_error)
        return
    actual = pyyaml.safe_load(source)
    assert incumbent.safe_dump(actual) == incumbent.safe_dump(expected)
    assert pyyaml.safe_dump(actual) == incumbent.safe_dump(actual)


def test_ruamel_is_the_incumbent_module_and_session_factory():
    require_backend("ruamel.yaml")
    from unstd.yaml import ruamel

    incumbent = importlib.import_module("ruamel.yaml")
    assert ruamel is incumbent
    assert ruamel.YAML is incumbent.YAML
    assert ruamel.composer.Composer is incumbent.composer.Composer


@pytest.mark.parametrize(
    ("backend", "module", "other"),
    [("pyyaml", "yaml", "ruamel.yaml"), ("ruamel", "ruamel.yaml", "yaml")],
)
def test_backend_imports_do_not_load_the_other_contract(backend, module, other):
    require_backend(module)
    program = f"""
import sys
from unstd import yaml
assert "yaml" not in sys.modules
assert "ruamel.yaml" not in sys.modules
assert "unstd.yaml.codec" not in sys.modules
from unstd.yaml import {backend}
assert {backend} is sys.modules[{module!r}]
assert {other!r} not in sys.modules
assert "unstd.yaml.codec" not in sys.modules
"""
    result = subprocess.run(  # noqa: S603 - fixed interpreter and parameterized import-only program.
        [sys.executable, "-c", program], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_exports_bind_the_actual_codec_functions_once():
    require_backend("ruamel.yaml")
    from unstd import yaml
    from unstd.yaml import codec

    assert yaml.loads is codec.loads
    assert yaml.dumps is codec.dumps
    assert yaml.__dict__["loads"] is codec.loads
