"""Packing evidence rejects incorrect results and mismatched source provenance."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tests.support.environment.backends import require_backend


REPO_ROOT = Path(__file__).resolve().parents[5]
_SPEC = importlib.util.spec_from_file_location(
    "_pack_backend_gate_profile", REPO_ROOT / "bench" / "profiles" / "pack_backend.py"
)
assert _SPEC is not None
assert _SPEC.loader is not None
profile = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = profile
_SPEC.loader.exec_module(profile)


@pytest.mark.parametrize("actual", [b"wrong", bytearray(b"expected"), "expected"])
def test_wrong_bytes_or_return_type_is_rejected_before_timing(actual, monkeypatch):
    def no_timing(*_args):
        pytest.fail("an incorrect operation reached the timing loop")

    monkeypatch.setattr(profile, "_batch", no_timing)
    case = profile.Case("fixture", lambda: actual, profile._equal(b"expected"), "")
    with pytest.raises(AssertionError, match="independent struct oracle"):
        profile._measure(case, 1)


def test_zero_copy_oracle_rejects_owned_or_writable_results():
    np = require_backend("numpy")
    from unstd.pack import binpack

    case = next(
        case for case in profile._cases(binpack) if case.name == "numpy/unpack-view/256"
    )
    actual = case.run()
    case.check(actual)
    with pytest.raises(AssertionError, match="zero-copy decode"):
        case.check(actual.copy())
    writable = np.frombuffer(bytearray(actual.tobytes()), dtype="<f8")
    with pytest.raises(AssertionError, match="zero-copy decode"):
        case.check(writable)
    corrupt = np.frombuffer(bytes(len(actual.tobytes())), dtype="<f8")
    with pytest.raises(AssertionError, match="zero-copy decode"):
        case.check(corrupt)


@pytest.mark.parametrize("legacy", [False, True])
def test_source_hashes_cover_internal_backend_and_package_root(tmp_path, legacy):
    root = tmp_path / "unstd"
    package = root / "pack"
    package.mkdir(parents=True)
    # Include retained snapshots as well as the current flat package.
    relative = "_internal/buffer.py" if legacy else "buffers.py"
    entrypoint = root / "__init__.py"
    entrypoint.write_text("# original root\n")
    (root / "pack" / "binpack.py").write_text("# public\n")
    helper = package / relative
    helper.parent.mkdir(exist_ok=True)
    helper.write_text("# original backend\n")
    before = profile._source_hashes(tmp_path)
    helper.write_text("# changed backend\n")
    entrypoint.write_text("# changed root\n")
    after = profile._source_hashes(tmp_path)
    assert before["unstd/pack/binpack.py"] == after["unstd/pack/binpack.py"]
    assert before["unstd/__init__.py"] != after["unstd/__init__.py"]
    path = f"unstd/pack/{relative}"
    assert before[path] != after[path]


def test_source_without_pack_implementation_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"must contain unstd/pack/binpack\.py"):
        profile._source_hashes(tmp_path)


def _worker(source: Path, case: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed local worker and isolated fixture
        [
            sys.executable,
            "-I",
            str(Path(profile.__file__).resolve()),
            "--worker",
            "--source",
            str(source),
            "--case",
            case,
            "--batch-ms",
            "0.01",
        ],
        cwd=source.parent,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


@pytest.fixture
def saved_source(tmp_path):
    require_backend("numpy")
    source = tmp_path / "saved" / "src"
    destination = source / "unstd"
    destination.mkdir(parents=True)
    original = profile._ROOT / "src" / "unstd"
    shutil.copyfile(original / "__init__.py", destination / "__init__.py")
    shutil.copytree(original / "pack", destination / "pack")
    return source


def test_fresh_worker_loads_selected_source_and_retains_hashes(saved_source):
    result = _worker(saved_source, "billy/http-length-pack")
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["source"] == str(saved_source.resolve())
    assert report["imported_source"] == str(
        (saved_source / "unstd" / "pack" / "binpack.py").resolve()
    )
    assert report["source_hashes"] == profile._source_hashes(saved_source)
    assert set(report["measurements"]) == {"billy/http-length-pack"}
    assert report["measurements"]["billy/http-length-pack"]["oracle_passed"]


def test_fresh_worker_rejects_unknown_selected_case(saved_source):
    result = _worker(saved_source, "unregistered-workload")
    assert result.returncode != 0
    assert "unknown cases: ['unregistered-workload']" in result.stderr


def test_fresh_worker_rejects_product_imported_from_another_tree(tmp_path):
    source = tmp_path / "selected"
    other = tmp_path / "other"
    for directory in (source, other):
        pack = directory / "unstd" / "pack"
        pack.mkdir(parents=True)
        (pack / "__init__.py").write_text("")
        (pack / "binpack.py").write_text("# fixture\n")
        (directory / "unstd" / "__init__.py").write_text("")
    (source / "unstd" / "__init__.py").write_text(
        f"__path__ = [{str(other / 'unstd')!r}]\n"
    )
    result = _worker(source, "billy/http-length-pack")
    assert result.returncode != 0
    assert "selected pack implementation was not imported" in result.stderr
