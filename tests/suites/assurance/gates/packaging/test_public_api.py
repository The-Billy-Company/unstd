"""Preserve consumer imports, exported names and package-owned call signatures.

The contract was captured before the internal layout changed. Upstream callable
signatures belong to their own supported dependency/interpreter versions; this
gate freezes only the signatures and identities owned by unstd itself.
"""

from __future__ import annotations

from functools import wraps
import gzip
from importlib import import_module
import inspect
import json
import pickle
import re
import sys
from types import FunctionType
import zlib

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


def _assert_signature(value, contract, *, signature=None) -> None:
    """Freeze owned parameters; verify native projections against their owner."""
    actual = inspect.signature(value, follow_wrapped=False)
    if reference := contract.get("native_constructor"):
        owner = getattr(import_module(reference["module"]), reference["attribute"])
        assert inspect.isclass(value)
        assert inspect.isclass(owner)
        assert issubclass(value, owner)
        for attribute in ("__init__", "__new__"):
            assert inspect.getattr_static(value, attribute) is inspect.getattr_static(
                owner, attribute
            )
        assert inspect.getattr_static(
            type(value), "__call__"
        ) is inspect.getattr_static(type(owner), "__call__")
        assert actual == inspect.signature(owner, follow_wrapped=False)
        return
    parameters = dict(actual.parameters)
    for name, reference in contract.get("native_defaults", {}).items():
        owner = getattr(import_module(reference["module"]), reference["attribute"])
        native = inspect.signature(owner, follow_wrapped=False).parameters[
            reference["parameter"]
        ]
        assert name in parameters
        parameter = parameters[name]
        assert parameter.default is native.default
        # Preserve the captured spelling while checking the live native value.
        parameters[name] = parameter.replace(default=reference["frozen_default"])
    actual = actual.replace(parameters=parameters.values())
    expected = signature if signature is not None else contract["signature"]
    assert _signature_spelling(str(actual)) == _signature_spelling(expected), (
        value,
        str(actual),
        expected,
    )


def _assert_contract(name, module) -> None:
    expected = _CONTRACT[name]
    exports = list(expected["exports"])
    for addition in expected.get("versioned_exports", []):
        if sys.version_info[:2] >= tuple(addition["since"]):
            exports.extend(addition["exports"])
    assert list(getattr(module, "__all__", [])) == exports
    for attribute, contract in expected["callables"].items():
        if sys.version_info[:2] < tuple(contract.get("since", (0,))):
            assert not hasattr(module, attribute)
            continue
        value = getattr(module, attribute)
        reference = contract.get("stdlib_alias")
        if reference and sys.version_info[:2] >= tuple(reference.get("since", (0,))):
            owner = getattr(import_module(reference["module"]), reference["attribute"])
            assert value is owner
            continue
        if name == "unstd.compress.gzipx" and module._backend is None:
            assert value is getattr(gzip, attribute)
            continue
        if name == "unstd.compress.zlibx" and module._backend is None:
            assert value is getattr(zlib, attribute)
            continue
        if (
            name == "unstd.toml"
            and attribute in {"dump", "dumps"}
            and not module._HAVE_TOMLKIT
        ):
            assert value.__module__ == "unstd.extras"
            with pytest.raises(ImportError, match="'toml' extra"):
                value()
            continue
        signature = contract["signature"]
        if name in {"unstd.rand", "unstd.rand.draw"} and not module.HAVE_NUMPY:
            signature = signature.replace("numpy.ndarray | ", "")
        _assert_signature(value, contract, signature=signature)
        assert value.__module__ == contract["module"]


@pytest.mark.parametrize("name", _CONTRACT)
def test_public_module_contract(name: str) -> None:
    module = pytest.importorskip(name, exc_type=ImportError)
    _assert_contract(name, module)


@pytest.mark.parametrize("name", ["unstd.time.timeutil", "unstd.yaml"])
def test_public_inventory_retains_all_exports(name: str) -> None:
    text = (REPO_ROOT / "docs/api.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith(f"| [`{name}`](")]
    assert len(rows) == 1, (name, rows)
    documented = re.findall(r"`([^`]+)`", rows[0].split("|")[2])
    assert documented == _CONTRACT[name]["exports"]


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


@pytest.mark.skipif(sys.version_info < (3, 15), reason="Python 3.15 stdlib aliases")
@pytest.mark.parametrize(
    ("module_name", "attribute"),
    [
        ("unstd.serde.b64", "b64encode"),
        ("unstd.serde.b64", "b64decode"),
        ("unstd.serde.b64", "standard_b64encode"),
        ("unstd.serde.b64", "standard_b64decode"),
        ("unstd.serde.b64", "urlsafe_b64encode"),
        ("unstd.compress.zlibx", "compressobj"),
        ("unstd.compress.zlibx", "adler32_combine"),
        ("unstd.compress.zlibx", "crc32_combine"),
    ],
)
def test_stdlib_alias_contract_rejects_wrapped_aliases(
    monkeypatch, module_name, attribute
):
    module = import_module(module_name)
    value = getattr(module, attribute)

    @wraps(value)
    def wrapped(*args, **kwargs):
        return value(*args, **kwargs)

    monkeypatch.setattr(module, attribute, wrapped)
    with pytest.raises(AssertionError):
        _assert_contract(module_name, module)


def test_process_result_pickle_keeps_its_public_identity() -> None:
    result = proc.CompletedProcess(("echo", "ok"), 0, "ok\n", "", 0.25)
    restored = pickle.loads(pickle.dumps(result))  # noqa: S301 — owned local value
    assert type(restored) is proc.CompletedProcess
    assert restored == result
    assert pickle.loads(pickle.dumps(proc.ProcessError)) is proc.ProcessError  # noqa: S301


@pytest.fixture
def compression_contract():
    from unstd.compress import gzipx

    if gzipx._backend is None:
        pytest.skip("the native compression function requires the compress extra")
    return gzipx.compress, _CONTRACT["unstd.compress.gzipx"]["callables"]["compress"]


def test_native_default_cannot_be_replaced_by_an_owned_value(compression_contract):
    value, contract = compression_contract
    changed = FunctionType(
        value.__code__, value.__globals__, value.__name__, value.__defaults__
    )
    changed.__annotations__ = dict(value.__annotations__)
    changed.__kwdefaults__ = {**value.__kwdefaults__, "mtime": object()}
    with pytest.raises(AssertionError):
        _assert_signature(changed, contract)


def test_wrapped_metadata_cannot_hide_an_added_owned_parameter(compression_contract):
    value, contract = compression_contract

    @wraps(value)
    def changed(
        data,
        compresslevel=value.__defaults__[0],
        *,
        mtime=value.__kwdefaults__["mtime"],
        added=None,
    ):
        return value(data, compresslevel, mtime=mtime)

    assert inspect.signature(changed) == inspect.signature(value)
    with pytest.raises(AssertionError):
        _assert_signature(changed, contract)


@pytest.fixture
def settings_contract():
    model = pytest.importorskip("unstd.model", exc_type=ImportError)
    return model.StrictSettings, _CONTRACT["unstd.model"]["callables"]["StrictSettings"]


def test_native_constructor_cannot_hide_owned_fields(settings_contract):
    value, contract = settings_contract

    class Expanded(value):
        added: int = 0

    assert Expanded.__init__ is value.__init__
    with pytest.raises(AssertionError):
        _assert_signature(Expanded, contract)


def test_native_constructor_requires_the_original_inheritance(settings_contract):
    value, contract = settings_contract
    detached = type(
        "Detached",
        (),
        {
            "__init__": value.__init__,
            "__new__": value.__new__,
            "__signature__": inspect.signature(value),
        },
    )
    with pytest.raises(AssertionError):
        _assert_signature(detached, contract)


def test_native_constructor_cannot_be_wrapped_with_the_same_signature(
    settings_contract,
):
    value, contract = settings_contract

    class Wrapped(value):
        def __init__(self, **values):
            super().__init__(**values)

    Wrapped.__signature__ = inspect.signature(value)
    with pytest.raises(AssertionError):
        _assert_signature(Wrapped, contract)


def test_native_constructor_cannot_change_its_binding_descriptor(settings_contract):
    value, contract = settings_contract

    class Unbound(value):
        __init__ = staticmethod(value.__init__)

    assert Unbound.__init__ is value.__init__
    with pytest.raises(AssertionError):
        _assert_signature(Unbound, contract)


def test_native_constructor_cannot_be_replaced_by_owned_allocation(settings_contract):
    value, contract = settings_contract

    class Allocated(value):
        def __new__(cls, *args, **kwargs):
            return super().__new__(cls)

    with pytest.raises(AssertionError):
        _assert_signature(Allocated, contract)


def test_native_constructor_cannot_be_replaced_by_an_owned_metaclass(settings_contract):
    value, contract = settings_contract

    class WrappedMeta(type(value)):
        def __call__(cls, *args, **kwargs):
            return super().__call__(*args, **kwargs)

    class Wrapped(value, metaclass=WrappedMeta):
        pass

    with pytest.raises(AssertionError):
        _assert_signature(Wrapped, contract)
