"""Every public package has checked, bounded workloads and frozen evidence."""

from __future__ import annotations

import copy
import importlib
import math
from pathlib import Path
import ssl
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest

from bench import speeds


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(scope="module")
def all_backends() -> None:
    """Optional local installs skip; required-extra CI rejects absence globally."""
    backends = speeds.environment()["backends"]
    assert isinstance(backends, dict)
    missing = sorted(name for name, version in backends.items() if version is None)
    if missing:
        pytest.skip(f"complete speed registry requires optional backends: {missing}")


@pytest.fixture(scope="module")
def complete_cases(all_backends: None) -> Iterator[list[speeds.Case]]:
    del all_backends
    with speeds.registry() as cases:
        yield cases


def test_every_public_operation_has_a_real_workload(
    complete_cases: list[speeds.Case],
) -> None:
    exclusions = speeds.check_coverage(complete_cases)
    public = speeds.public_apis()
    covered = {case.name.split("#", 1)[0] for case in complete_cases}
    assert covered | exclusions.keys() == public
    assert not covered & exclusions.keys()


def test_each_source_package_has_checked_workloads(
    complete_cases: list[speeds.Case],
) -> None:
    source = speeds.ROOT / "src/unstd"
    packages = {
        path.name
        for path in source.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }
    assert {case.package for case in complete_cases} == packages
    assert all(case.note.strip() for case in complete_cases)


def test_exclusions_name_imported_vocabulary_with_a_reason(
    complete_cases: list[speeds.Case],
) -> None:
    for qualified, reason in speeds.check_coverage(complete_cases).items():
        module_name, name = qualified.rsplit(".", 1)
        value = getattr(importlib.import_module(module_name), name)
        assert not getattr(value, "__module__", "").startswith("unstd.")
        assert len(reason.split()) >= 5, (qualified, reason)


def test_every_workload_has_complete_frozen_measurement_evidence(
    complete_cases: list[speeds.Case],
) -> None:
    baseline = speeds.load_baseline()
    speeds.validate_baseline(complete_cases, baseline)
    assert set(baseline["cases"]) == {case.name for case in complete_cases}
    for row in baseline["cases"].values():
        assert type(row["iterations"]) is int
        assert row["iterations"] > 0
        assert type(row["rounds"]) is int
        # The shared timer bounds exceptionally long batches to two rounds.
        assert row["rounds"] >= 2
        assert math.isfinite(row["spread"])
        assert row["spread"] >= 0
        assert row["measured_ns"] <= row["ceiling_ns"]


def test_real_workload_oracles_survive_repeated_audits(
    complete_cases: list[speeds.Case],
) -> None:
    speeds.audit(complete_cases)
    speeds.audit(complete_cases)


def test_tls_workload_accepts_lazy_default_roots_and_rejects_insecure_policy(
    all_backends: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del all_backends
    from bench.workloads import speed_cases_runtime

    def lazy_context(*, cafile: str | None = None) -> ssl.SSLContext:
        assert cafile is None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(capath=str(tmp_path))
        return context

    monkeypatch.setattr(ssl, "create_default_context", lazy_context)
    case = next(
        case
        for case in speed_cases_runtime._crypto()
        if case.name == "unstd.crypto.tls.client_context"
    )
    context = case.run()
    assert isinstance(context, ssl.SSLContext)
    assert context.cert_store_stats()["x509_ca"] == 0
    case.check(context)
    context.check_hostname = False
    with pytest.raises(AssertionError):
        case.check(context)
    context.check_hostname = True
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with pytest.raises(AssertionError):
        case.check(context)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with pytest.raises(AssertionError):
        case.check(context)


@pytest.mark.parametrize(
    ("module_name", "name"),
    [
        ("unstd.serde.jsonx", "canonical_strict"),
        ("unstd.crypto.token", "prepare_verifier"),
        *(
            ("unstd.time.timeutil", name)
            for name in (
                "wall_ns",
                "perf_ns",
                "process_time",
                "process_time_ns",
                "thread_time",
                "thread_time_ns",
                "sleep",
                "now",
                "date_today",
                "datetime_today",
                "from_iso",
                "date_from_iso",
                "time_from_iso",
                "from_timestamp",
            )
        ),
    ],
)
def test_only_comparison_registry_accepts_apis_missing_from_an_older_revision(
    all_backends: None, monkeypatch: pytest.MonkeyPatch, module_name: str, name: str
) -> None:
    del all_backends
    module = importlib.import_module(module_name)
    monkeypatch.delattr(module, name)
    with speeds.registry(comparison=True) as cases:
        assert f"{module_name}.{name}" not in {case.name for case in cases}
    with pytest.raises(AttributeError, match=name), speeds.registry():
        pass


def test_filesystem_workloads_reuse_bounded_private_storage(
    all_backends: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del all_backends
    from bench.workloads import speed_cases_operations

    owned: list[Path] = []

    def private_directory(*, prefix: str) -> TemporaryDirectory[str]:
        temporary = TemporaryDirectory(prefix=prefix, dir=tmp_path)
        owned.append(Path(temporary.name))
        return temporary

    monkeypatch.setattr(speed_cases_operations, "TemporaryDirectory", private_directory)

    def storage() -> tuple[int, int]:
        paths = [path for root in owned for path in root.rglob("*")]
        return len(paths), sum(path.stat().st_size for path in paths if path.is_file())

    with speeds.registry(["fs"]) as cases:
        assert owned
        speeds.audit(cases)
        settled = storage()
        for _ in range(4):
            speeds.audit(cases)
            assert storage() == settled
    assert all(not root.exists() for root in owned)
    assert not list(tmp_path.iterdir())


def test_registry_restores_caller_random_state_on_failed_run(
    all_backends: None,
) -> None:
    del all_backends
    from unstd.rand import draw

    py_state, numpy_engine = draw._py.getstate(), draw._gen
    assert numpy_engine is not None
    numpy_state = copy.deepcopy(numpy_engine.bit_generator.state)

    def fail_measurement() -> None:
        with speeds.registry(["rand"]) as cases:
            speeds.audit(cases)
            message = "injected failed measurement"
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match="injected failed measurement"):
        fail_measurement()
    assert draw._py.getstate() == py_state
    assert draw._gen is numpy_engine
    assert numpy_engine.bit_generator.state == numpy_state
