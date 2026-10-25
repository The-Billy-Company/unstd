"""Build and test an isolated proc kernel with reproducible sanitizer workers.

The generated interpreter wrapper restores the source and loader selection in
fixture-owned children. Linux sanitizer controllers use fork instead of CPython's
uninstrumented vfork path; admitted native proc launches retain posix_spawn.
Production modules and interpreter state are never modified.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import sysconfig
from tempfile import mkdtemp


_ROOT = Path(__file__).resolve().parents[3]
_TESTS = "tests/suites/product/proc"
_BOOTSTRAP = """import os, sys
sys.executable = {wrapper!r}
if {sanitize!r}:
    if sys.platform == 'darwin':
        # dyld removes the original insertion variable after interpreter startup.
        os.environ['DYLD_INSERT_LIBRARIES'] = {runtime!r}
    elif sys.platform == 'linux':
        import subprocess
        subprocess._USE_POSIX_SPAWN = False
        original_execute = subprocess.Popen._execute_child
        def no_op():
            pass
        def fork_controller(self, *arguments):
            # Only the stdlib seam is changed. The proc override invokes its C
            # kernel before reaching this fallback, so native spawn stays live.
            if len(arguments) == 23 and arguments[2] is None:
                arguments = list(arguments)
                arguments[2] = no_op
            return original_execute(self, *arguments)
        subprocess.Popen._execute_child = fork_controller
"""


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Capture bounded qualification output without changing the controller."""
    return subprocess.run(  # noqa: S603 — fixed local compiler/interpreter argv
        command, cwd=cwd, capture_output=True, text=True, timeout=180, check=False
    )


def _hashes(directory: Path) -> dict[str, str]:
    """Fingerprint source inputs without generated files or loaded binaries."""
    return {
        str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.suffix in {".py", ".pyi", ".c", ".h"}
    }


def _runtime(compiler: str) -> Path:
    """Resolve the runtime from the compiler used by the extension build."""
    name = (
        "libclang_rt.asan_osx_dynamic.dylib"
        if sys.platform == "darwin"
        else "libasan.so"
    )
    result = _run([compiler, f"-print-file-name={name}"], cwd=_ROOT)
    path = Path(result.stdout.strip())
    if result.returncode or not path.is_file():
        message = f"sanitizer runtime unavailable: {result.stdout} {result.stderr}"
        raise RuntimeError(message)
    return path.resolve()


def main() -> None:
    """Retain source, binary, loader and test receipts even when a test fails."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--junitxml", type=Path)
    parser.add_argument("--tests", default=_TESTS, choices=[_TESTS])
    args = parser.parse_args()
    if sys.platform not in {"linux", "darwin"}:
        parser.error("the isolated proc audit supports Linux and macOS")
    output = (
        args.output.resolve() if args.output else Path(mkdtemp(prefix="proc-memory-"))
    )
    if output == _ROOT or any(
        output.is_relative_to(_ROOT / name) for name in ("src", "native", "tests")
    ):
        parser.error("audit output must be separate from production source directories")
    output.mkdir(parents=True, exist_ok=True)
    package = output / "src/unstd"
    package.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_ROOT / "src/unstd/__init__.py", package / "__init__.py")
    shutil.copytree(
        _ROOT / "src/unstd/proc",
        package / "proc",
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*.so", "*.pyd", "__pycache__"),
    )
    native_hashes = _hashes(_ROOT / "native/proc")
    python_hashes = _hashes(package)
    build = [
        sys.executable,
        str(_ROOT / "native/proc/experiments/build.py"),
        "--warnings-as-errors",
        "--output",
        str(output / "src"),
    ]
    if args.sanitize:
        build.append("--sanitize")
    built = _run(build, cwd=_ROOT)
    (output / "build.txt").write_text(built.stdout + built.stderr)
    if built.returncode:
        print(built.stdout + built.stderr, file=sys.stderr)
        raise SystemExit(built.returncode)
    compiler = shlex.split(os.environ.get("CC") or sysconfig.get_config_var("CC"))[0]
    runtime = _runtime(compiler) if args.sanitize else None
    bootstrap = output / "bootstrap"
    bootstrap.mkdir(exist_ok=True)
    wrapper = output / "python"
    bootstrap_source = _BOOTSTRAP.format(
        wrapper=str(wrapper),
        sanitize=args.sanitize,
        runtime=str(runtime),
    )
    (bootstrap / "sitecustomize.py").write_text(bootstrap_source)
    environment = {
        "PYTHONPATH": os.pathsep.join(map(str, (output / "src", bootstrap, _ROOT))),
        "PYTHONMALLOC": "malloc" if args.sanitize else "debug",
    }
    if args.sanitize:
        environment.update(
            {
                "DYLD_INSERT_LIBRARIES"
                if sys.platform == "darwin"
                else "LD_PRELOAD": str(runtime),
                "ASAN_OPTIONS": "detect_leaks=0:abort_on_error=1:strict_string_checks=1",
                "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
                "UNSTD_NATIVE_SANITIZERS": "1",
            }
        )
    assignments = " ".join(
        shlex.quote(f"{key}={value}") for key, value in environment.items()
    )
    wrapper.write_text(
        "#!/bin/sh\nexec env "
        + assignments
        + " "
        + shlex.quote(sys.executable)
        + ' "$@"\n'
    )
    wrapper.chmod(0o755)
    extension = (
        package / "proc" / ("_proc_native" + sysconfig.get_config_var("EXT_SUFFIX"))
    )
    proof = f"""import sys
from unstd import proc
from unstd.proc._internal import backend
assert backend._native.__file__ == {str(extension)!r}, backend._native.__file__
routes = []
spawn = backend._native.spawn
def observe(*args):
    outcome = spawn(*args)
    routes.append(outcome is not NotImplemented)
    return outcome
backend._native.spawn = observe
assert proc.capture([sys.executable, '-c', "print('native audit')"]) == 'native audit'
assert routes == [True], routes
"""
    verified = _run([str(wrapper), "-c", proof], cwd=output)
    xml = args.junitxml.resolve() if args.junitxml else output / "tests.xml"
    xml.parent.mkdir(parents=True, exist_ok=True)
    command = [
        str(wrapper),
        "-m",
        "pytest",
        str(_ROOT / args.tests),
        "-q",
        "--tb=short",
        f"--junitxml={xml}",
    ]
    # The wrapper restores loader/source variables in child environments. The
    # exact environment-isolation contract runs in ordinary installed tests.
    selection = "not hostile_argv"
    if args.sanitize:
        # ASan reserves virtual address ranges independently of capture buffers.
        selection += " and not allocation_limit"
    command.extend(["-k", selection])
    tested = _run(command, cwd=output) if verified.returncode == 0 else verified
    (output / "tests.txt").write_text(tested.stdout + tested.stderr)
    unchanged = native_hashes == _hashes(
        _ROOT / "native/proc"
    ) and python_hashes == _hashes(package)
    exit_code = tested.returncode if unchanged else 1
    manifest = {
        "python": sys.version,
        "platform": platform.platform(),
        "sanitize": args.sanitize,
        "native_source_hashes": native_hashes,
        "python_source_hashes": python_hashes,
        "sources_unchanged": unchanged,
        "extension_sha256": sha256(extension.read_bytes()).hexdigest(),
        "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "bootstrap_sha256": sha256(bootstrap_source.encode()).hexdigest(),
        "build_command": build,
        "test_command": command,
        "environment": environment,
        "public_native_route_verified": verified.returncode == 0,
        "verification_output": verified.stdout + verified.stderr,
        "test_exit_code": tested.returncode,
        "qualification_exit_code": exit_code,
        "junitxml": str(xml),
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(tested.stdout + tested.stderr)
    print(json.dumps(manifest, indent=2))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
