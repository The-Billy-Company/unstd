"""Billy filesystem evidence admits checked source and real atomic effects."""

from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from bench.profiles import billy_fs as profile
from unstd import fs


_HOSTED = """
def land_state(user_id: str, raw: str) -> str:
    encoded = raw.encode()
    if len(encoded) > _STATE_MAX_BYTES:
        raise ValueError("per-state ceiling")
    path = state_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    occupied = sum(item.stat().st_size for item in profiles_root().glob("*/state.json")
                   if item != path and item.is_file())
    if occupied + len(encoded) > _STATE_CACHE_BYTES:
        raise ValueError("cache ceiling")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".state-", delete=False) as out:
        pending = Path(out.name)
        try:
            out.write(encoded)
            out.flush()
            os.fsync(out.fileno())
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)
    return str(path)
"""
_NOTEBOOK = """
def atomic(path: Path, value: bytes) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
"""


@pytest.fixture
def captured(tmp_path):
    root = tmp_path / "billy"
    for key, (relative, symbol) in profile._CONSUMERS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        source = {
            "hosted": _HOSTED,
            "notebook": _NOTEBOOK,
        }.get(key, f"def {symbol}():\n    return None\n")
        path.write_text(source, encoding="utf-8")
    for key, relative in profile._INPUTS.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            '{"nested":{"name":"PRIVATE SOURCE VALUE"},"rows":[1,true]}'
            if key == "report"
            else "[importlinter]\nroot_package=synthetic\n",
            encoding="utf-8",
        )
    output = tmp_path / "fixtures.json"
    profile._save_fixtures(root, output)
    return root, output


def test_fixtures_keep_schema_and_lengths_without_capturing_string_values(captured):
    root, path = captured
    fixture = profile._load_fixtures(path, root)
    report = next(case for case in fixture["cases"] if case["kind"] == "report")
    assert report["value"]["nested"]["name"] == "x" * len("PRIVATE SOURCE VALUE")
    assert report["value"]["rows"] == [1, True]
    assert "PRIVATE SOURCE VALUE" not in path.read_text()
    config = next(case for case in fixture["cases"] if case["consumer"] == "config")
    assert config["encoded_bytes"] == (root / profile._INPUTS["config"]).stat().st_size


def test_changed_consumer_source_is_rejected(captured):
    root, path = captured
    relative, _symbol = profile._CONSUMERS["hosted"]
    with (root / relative).open("a", encoding="utf-8") as source:
        source.write("\n# concurrent source edit\n")
    with pytest.raises(ValueError, match="consumer source drifted"):
        profile._load_fixtures(path, root)


def test_corrupted_payload_recipe_is_rejected(captured):
    root, path = captured
    fixture = json.loads(path.read_text())
    fixture["cases"][0]["line"] = "wrong"
    path.write_text(json.dumps(fixture))
    with pytest.raises(ValueError, match="corrupt fs payload"):
        profile._load_fixtures(path, root)


def test_byte_ceiling_scenarios_use_encoded_bytes_and_valid_browser_json(captured):
    root, path = captured
    fixture = profile._load_fixtures(path, root)
    for recipe in fixture["cases"]:
        if recipe["kind"] == "browser":
            raw = profile._payload(recipe)
            assert len(raw.encode()) == recipe["bytes"]
            assert len(raw.encode()) <= 4 << 20
            assert json.loads(raw)["cookies"][0]["domain"] == "example.invalid"


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem modes")
def test_shared_mutation_paths_and_exact_billy_ports_are_repeatable(captured, tmp_path):
    root, path = captured
    fixture = profile._load_fixtures(path, root)
    wanted = {
        "oauth-token",
        "capture-report",
        "browser-land-65536-peers-0",
        "notebook-checkpoint-4096",
    }
    fixture["cases"] = [case for case in fixture["cases"] if case["name"] in wanted]
    prepared = profile._prepare(fixture, root, tmp_path / "work", fs, None)
    assert len(prepared) == len(wanted)
    for case in prepared:
        for label, operation in case.calls.items():
            for _ in range(2):
                case.check(label, operation())
        assert case.path.read_bytes() == case.expected
        assert stat.S_IMODE(case.path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem modes")
def test_hosted_candidate_retains_original_budget_checks_before_any_write(
    captured, tmp_path
):
    root, path = captured
    fixture = profile._load_fixtures(path, root)
    fixture["cases"] = [
        case
        for case in fixture["cases"]
        if case["name"] == "browser-land-65536-peers-0"
    ]
    reached = []

    def forbidden(*_args, **_kwargs):
        reached.append("writer")
        pytest.fail("a refused state reached the writer")

    candidate = profile._prepare(
        fixture, root, tmp_path / "work", SimpleNamespace(atomic_write=forbidden), None
    )[0]
    blocked = candidate.path.parent.parent / "other" / "state.json"
    blocked.parent.mkdir()
    with blocked.open("wb") as stream:
        stream.truncate(64 << 20)  # sparse file; stat size alone fills the quota
    with pytest.raises(ValueError, match="cache ceiling"):
        candidate.calls["python"]()
    assert reached == []
    assert candidate.path.read_bytes() == b"previous"


def test_native_loader_rejects_an_ordinary_python_implementation(tmp_path):
    path = tmp_path / "_fs_experiment.py"
    path.write_text("def atomic_write(*args, **kwargs): pass\n")
    with pytest.raises(ValueError, match="compiled _fs_experiment"):
        profile._load_native(path)


def test_declined_native_work_is_rejected(captured, tmp_path):
    root, path = captured
    fixture = profile._load_fixtures(path, root)
    fixture["cases"] = fixture["cases"][:1]
    native = SimpleNamespace(atomic_write=lambda *_args, **_kwargs: NotImplemented)
    prepared = profile._prepare(fixture, root, tmp_path / "work", fs, native)[0]
    with pytest.raises(RuntimeError, match="declined"):
        prepared.calls["native"]()
    assert prepared.path.read_bytes() == b"previous"


def test_native_no_op_cannot_borrow_a_successful_python_write(
    captured, tmp_path, monkeypatch
):
    root, path = captured
    fixture = profile._load_fixtures(path, root)
    fixture["cases"] = fixture["cases"][:1]
    native = SimpleNamespace(atomic_write=lambda *_args, **_kwargs: None)
    prepared = profile._prepare(fixture, root, tmp_path / "work", fs, native)[0]

    def forbidden(*_args, **_kwargs):
        pytest.fail("a no-op writer entered timing")

    monkeypatch.setattr(profile._BASE, "_batch", forbidden)
    with pytest.raises(AssertionError):
        profile._measure(prepared, rounds=1, repetitions=1)
    assert prepared.path.read_bytes() == b"uncommitted-state-sentinel"


def test_wrong_file_bytes_or_permissions_fail_the_independent_check(tmp_path):
    path = tmp_path / "state.json"
    path.write_bytes(b"wrong")
    path.chmod(0o600)
    prepared = profile.Prepared({}, path, b"expected", {})
    with pytest.raises(AssertionError):
        prepared.check("python", path)
    path.write_bytes(b"expected")
    path.chmod(0o644)
    with pytest.raises(AssertionError):
        prepared.check("python", path)


def test_paired_result_retains_cpu_and_wall_ratios_separately(monkeypatch, tmp_path):
    def measure(case, **_kwargs):
        assert list(case.calls) == ["python", "native"]
        return {
            "before_over_current": {},
            "median_per_operation_ns": {
                "python": {"wall_ns": 20, "process_cpu_ns": 8},
                "native": {"wall_ns": 10, "process_cpu_ns": 16},
            },
        }

    monkeypatch.setattr(profile._BASE, "_measure", measure)
    prepared = profile.Prepared(
        {"name": "proof"},
        tmp_path,
        b"",
        {"python": lambda: None, "native": lambda: None},
    )
    result = profile._measure(prepared, rounds=1, repetitions=1)
    assert result["python_over_native"] == {"wall_ns": 2, "process_cpu_ns": 0.5}
