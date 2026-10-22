"""Cold imports avoid distribution discovery while version and codec contracts hold.

Fresh workers expose import costs that an already-collected pytest process hides.
An import blocker enforces the startup invariant without machine-dependent timing
thresholds; native msgspec objects remain the parsing and encoding oracle.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from tests.support.environment.backends import require_backend
from tests.support.environment.process import run_python


if TYPE_CHECKING:
    from pathlib import Path


_BLOCK_METADATA = """\
import sys

assert "importlib.metadata" not in sys.modules

class BlockMetadata:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "importlib.metadata" or fullname.startswith("importlib.metadata."):
            raise AssertionError("ordinary imports must not load package metadata")
        return None

sys.meta_path.insert(0, BlockMetadata())
"""


def _run(code: str, tmp_path: Path) -> None:
    result = run_python(textwrap.dedent(code), cwd=tmp_path, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_core_and_serde_imports_do_not_load_package_metadata(tmp_path: Path) -> None:
    _run(
        _BLOCK_METADATA
        + textwrap.dedent(
            """\
            import unstd
            from unstd import fs, proc
            from unstd.serde import jsonx, ndjson

            assert "__version__" not in vars(unstd)
            assert jsonx.loads('{"value": 1}') == {"value": 1}
            assert ndjson.loads('1\\n2\\n') == [1, 2]
            assert "importlib.metadata" not in sys.modules
            """
        ),
        tmp_path,
    )


def test_codec_reuses_native_methods_without_loading_package_metadata(
    tmp_path: Path,
) -> None:
    require_backend("msgspec")
    _run(
        _BLOCK_METADATA
        + textwrap.dedent(
            """\
            import msgspec
            from unstd.serde.structs import Codec

            class Record(msgspec.Struct):
                count: int
                label: str
                values: list[float] = []

            codec = Codec(Record)
            decoder = codec.decode.__self__
            encoder = codec.encode.__self__
            assert type(decoder) is msgspec.json.Decoder
            assert type(encoder) is msgspec.json.Encoder
            assert decoder.type is Record
            assert codec.decode is codec.decode
            assert codec.encode is codec.encode

            oracle = msgspec.json.Decoder(Record)
            raw = '{"count":2,"label":"café","values":[1,2.5]}'.encode()
            for payload in (raw, raw.decode(), '{"count":1,"label":"x","extra":true}'):
                assert codec.decode(payload) == oracle.decode(payload)
                assert codec.decode.__self__ is decoder
            value = codec.decode(raw)
            assert codec.encode(value) == msgspec.json.Encoder().encode(value)
            assert codec.encode.__self__ is encoder

            for payload in (
                b'{',
                b'{"count":"2","label":"x"}',
                b'{"count":2}',
                b'{"count":2,"label":"x"} trailing',
                b'{"count":2,"label":"\\xff"}',
            ):
                errors = []
                for decode in (oracle.decode, codec.decode):
                    try:
                        decode(payload)
                    except (msgspec.DecodeError, UnicodeDecodeError) as error:
                        errors.append((type(error), str(error)))
                    else:
                        raise AssertionError("invalid payload was accepted")
                assert errors[0] == errors[1]
            assert "importlib.metadata" not in sys.modules
            """
        ),
        tmp_path,
    )


@pytest.mark.parametrize("installed", [True, False], ids=["installed", "source"])
def test_version_lookup_is_lazy_and_cached(installed: bool, tmp_path: Path) -> None:
    _run(
        f"installed = {installed!r}\n"
        + textwrap.dedent(
            """\
            from importlib import metadata

            calls = []

            def version(name):
                calls.append(name)
                if not installed:
                    raise metadata.PackageNotFoundError(name)
                return "42.7"

            metadata.version = version
            import unstd

            assert calls == []
            assert "__version__" not in vars(unstd)
            assert "__version__" in dir(unstd)
            assert calls == []
            expected = "42.7" if installed else "0.0.0.dev0"
            from unstd import __version__

            assert __version__ == expected
            assert unstd.__version__ == expected
            assert vars(unstd)["__version__"] == expected
            assert calls == ["unstd"]
            """
        ),
        tmp_path,
    )


def test_unknown_attributes_and_dir_do_not_load_package_metadata(
    tmp_path: Path,
) -> None:
    _run(
        _BLOCK_METADATA
        + textwrap.dedent(
            """\
            import unstd

            assert "__version__" in dir(unstd)
            assert "__version__" not in vars(unstd)
            assert not hasattr(unstd, "missing_attribute")
            try:
                unstd.missing_attribute
            except AttributeError as error:
                assert str(error) == "module 'unstd' has no attribute 'missing_attribute'"
            else:
                raise AssertionError("unknown attribute was accepted")
            assert "importlib.metadata" not in sys.modules
            """
        ),
        tmp_path,
    )


def test_unexpected_version_lookup_errors_propagate_without_caching(
    tmp_path: Path,
) -> None:
    _run(
        """\
        from importlib import metadata

        failure = RuntimeError("broken distribution metadata")
        calls = []

        def version(name):
            calls.append(name)
            raise failure

        metadata.version = version
        import unstd

        assert calls == []
        for _ in range(2):
            try:
                unstd.__version__
            except RuntimeError as error:
                assert error is failure
            else:
                raise AssertionError("metadata error was suppressed")
            assert "__version__" not in vars(unstd)
        assert calls == ["unstd", "unstd"]
        """,
        tmp_path,
    )


def test_reload_refreshes_the_cached_version_lazily(tmp_path: Path) -> None:
    _run(
        """\
        import importlib
        from importlib import metadata

        calls = []

        def version(name):
            calls.append(name)
            return f"42.{len(calls)}"

        metadata.version = version
        import unstd

        assert unstd.__version__ == "42.1"
        assert importlib.reload(unstd) is unstd
        assert calls == ["unstd"]
        assert "__version__" not in vars(unstd)
        assert "__version__" in dir(unstd)
        assert unstd.__version__ == "42.2"
        assert unstd.__version__ == "42.2"
        assert calls == ["unstd", "unstd"]
        """,
        tmp_path,
    )
