"""Preserve consumer imports, exported names and package-owned call signatures.

The contract was captured before the internal layout changed. Upstream callable
signatures belong to their own supported dependency/interpreter versions; this
gate freezes only the signatures and identities owned by unstd itself.
"""

from __future__ import annotations

import gzip
import inspect
import json
import pickle
import re

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.support.environment.process import run_python
from unstd import proc


_CONTRACT = json.loads(
    (REPO_ROOT / "tests/support/packaging/public_api.json").read_text(encoding="utf-8")
)


def _signature_spelling(signature: str) -> str:
    # Python 3.14 unified typing.Union with types.UnionType; the accepted
    # values and parameter contract are identical across this spelling change.
    return re.sub(r"Optional\[(Literal\[[^\]]+\])\]", r"\1 | None", signature)


def _assert_contract(name, module) -> None:
    expected = _CONTRACT[name]
    assert list(getattr(module, "__all__", [])) == expected["exports"]
    for attribute, contract in expected["callables"].items():
        value = getattr(module, attribute)
        if name == "unstd.compress.gzipx" and module._backend is None:
            assert value is getattr(gzip, attribute)
            continue
        if (
            name == "unstd.toml"
            and attribute in {"dump", "dumps"}
            and not module._HAVE_TOMLKIT
        ):
            assert value.__module__ == "unstd._extra"
            with pytest.raises(ImportError, match="'toml' extra"):
                value()
            continue
        signature = contract["signature"]
        if name in {"unstd.rand", "unstd.rand.draw"} and not module.HAVE_NUMPY:
            signature = signature.replace("numpy.ndarray | ", "")
        assert _signature_spelling(
            str(inspect.signature(value))
        ) == _signature_spelling(signature), (
            name,
            attribute,
            str(inspect.signature(value)),
            signature,
        )
        assert value.__module__ == contract["module"]


@pytest.mark.parametrize("name", _CONTRACT)
def test_public_module_contract(name: str) -> None:
    module = pytest.importorskip(name, exc_type=ImportError)
    _assert_contract(name, module)


def test_contract_with_optional_backends_unavailable(tmp_path) -> None:
    code = """
import importlib
import sys

from tests.suites.assurance.gates.packaging.test_public_api import (
    _CONTRACT, _assert_contract,
)

blocked = {
    'orjson', 'msgspec', 'pybase64', 'uuid_utils', 'blake3', 'whenever',
    'pydantic', 'pydantic_settings', 'irgx', 'sortedcontainers', 'immutables',
    'numpy', 'rapidfuzz', 'soundfile', 'tomlkit', 'ruamel', 'zlib_ng',
}

class MissingBackends:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in blocked:
            raise ModuleNotFoundError(fullname)

for name in list(sys.modules):
    if name.partition('.')[0] in blocked or name.startswith('unstd.'):
        del sys.modules[name]
sys.meta_path.insert(0, MissingBackends())
for name in _CONTRACT:
    try:
        module = importlib.import_module(name)
    except ImportError as error:
        assert 'extra' in str(error), (name, error)
    else:
        _assert_contract(name, module)
"""
    result = run_python(code, cwd=tmp_path, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_bindings_are_not_public_exports() -> None:
    for name, contract in _CONTRACT.items():
        assert not any("native" in export.lower() for export in contract["exports"]), (
            name
        )


def test_process_result_pickle_keeps_its_public_identity() -> None:
    result = proc.CompletedProcess(("echo", "ok"), 0, "ok\n", "", 0.25)
    restored = pickle.loads(pickle.dumps(result))  # noqa: S301 — owned local value
    assert type(restored) is proc.CompletedProcess
    assert restored == result
    assert pickle.loads(pickle.dumps(proc.ProcessError)) is proc.ProcessError  # noqa: S301
