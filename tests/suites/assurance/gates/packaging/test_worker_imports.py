"""Workers keep the parent's product while retaining repository support imports."""

from importlib.machinery import ModuleSpec
import json
import subprocess

import pytest

from tests.support.environment import process as process_support


@pytest.mark.parametrize("parent", ["checkout", "installed", "extracted"])
def test_worker_preserves_parent_product_origin(parent, tmp_path, monkeypatch):
    repo = tmp_path / "checkout"
    source = repo / "src"
    installed = tmp_path / "installed"
    extracted = tmp_path / "extracted wheel"
    for directory, marker in (
        (source, "checkout"),
        (installed, "installed"),
        (extracted, "extracted"),
    ):
        package = directory / "unstd"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"origin = {marker!r}\n")
    (repo / "support_probe.py").write_text("available = True\n")
    selected = {"checkout": source, "installed": installed, "extracted": extracted}
    parent_origin = selected[parent] / "unstd/__init__.py"
    monkeypatch.setattr(process_support, "REPO_ROOT", repo)
    monkeypatch.setattr(
        process_support,
        "find_spec",
        lambda name: ModuleSpec(name, loader=None, origin=str(parent_origin)),
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    command = process_support.python_argv(
        f"import sys; sys.path.append({str(source if parent == 'installed' else installed)!r})\n"
        "import json, unstd, support_probe\n"
        "print(json.dumps([unstd.origin, unstd.__file__, support_probe.available, sys.argv[1:]]))",
        ["retained"],
    )
    # Disable host site packages so the two fixture distributions are the only
    # candidates, while retaining the worker's actual import prelude.
    result = subprocess.run(  # noqa: S603 — current interpreter, fixture-only code
        [command[0], "-I", "-S", *command[1:]],
        cwd=elsewhere,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [parent, str(parent_origin), True, ["retained"]]


@pytest.mark.parametrize("instrumented", [None, "0", "1"])
def test_explicit_worker_environment_retains_only_armed_sanitizers(
    instrumented, tmp_path, monkeypatch
):
    runtime = {
        "LD_PRELOAD": "",
        "ASAN_OPTIONS": "detect_leaks=0",
        "UBSAN_OPTIONS": "print_stacktrace=1:halt_on_error=1",
        "PYTHONMALLOC": "malloc",
    }
    for name, value in runtime.items():
        monkeypatch.setenv(name, value)
    if instrumented is None:
        monkeypatch.delenv("UNSTD_NATIVE_SANITIZERS", raising=False)
    else:
        monkeypatch.setenv("UNSTD_NATIVE_SANITIZERS", instrumented)
    names = [*runtime, "UNSTD_NATIVE_SANITIZERS", "EXPLICIT_SCENARIO_VALUE"]
    code = (
        "import json, os\n"
        f"print(json.dumps({{name: os.environ[name] for name in {names!r} if name in os.environ}}))"
    )
    result = process_support.run_python(
        code,
        cwd=tmp_path,
        env=process_support.isolated_environment(EXPLICIT_SCENARIO_VALUE="retained"),
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    expected = {"EXPLICIT_SCENARIO_VALUE": "retained"}
    if instrumented == "1":
        expected.update(runtime)
        expected["UNSTD_NATIVE_SANITIZERS"] = "1"
    assert json.loads(result.stdout) == expected
