"""Build an isolated clone kernel for GC, allocation-failure and sanitizer audits.

Run with the interpreter being audited. The generated ``python`` wrapper keeps
the same instrumented kernel and sanitizer runtime in clean-environment child
processes. Production sources and extension binaries are never overwritten.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
from tempfile import mkdtemp


_ROOT = Path(__file__).resolve().parents[3]
_SEQUENCE_ALLOCATION = "tuple ? PyTuple_New(size) : PyList_New(size)"


def _run(
    arguments: list[str], *, capture_output: bool = False, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — compiler/interpreter argv, no shell
        arguments,
        check=True,
        timeout=120,
        text=True,
        capture_output=capture_output,
        cwd=cwd,
    )


def _sanitizer_runtime(compiler: str) -> Path:
    name = (
        "libclang_rt.asan_osx_dynamic.dylib"
        if sys.platform == "darwin"
        else "libasan.so"
    )
    reported = _run([compiler, f"-print-file-name={name}"], capture_output=True)
    candidate = Path(reported.stdout.strip())
    if candidate.is_file():
        return candidate.resolve()
    message = f"address sanitizer runtime unavailable: {reported.stdout.strip()}"
    raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force-gc", action="store_true")
    parser.add_argument("--sanitize", action="store_true")
    arguments = parser.parse_args()
    if os.name != "posix":
        parser.error("this native audit currently requires a POSIX compiler")
    output = (
        arguments.output.resolve()
        if arguments.output
        else Path(mkdtemp(prefix="unstd-clone-memory-"))
    )
    if (
        output == _ROOT
        or output.is_relative_to(_ROOT / "src")
        or output.is_relative_to(_ROOT / "native")
    ):
        parser.error("audit output must be separate from production source directories")
    source_root = output / "src"
    package = source_root / "unstd"
    native = package / "clone"
    native.mkdir(parents=True, exist_ok=True)
    for source, target in (
        (_ROOT / "src/unstd/__init__.py", package / "__init__.py"),
        (_ROOT / "src/unstd/clone/__init__.py", native / "__init__.py"),
        (_ROOT / "src/unstd/clone/structural.py", native / "structural.py"),
    ):
        shutil.copy2(source, target)
    original = (_ROOT / "native/clone/src/adapter.c").read_bytes()
    injected = original.decode()
    if arguments.force_gc:
        if injected.count(_SEQUENCE_ALLOCATION) != 1:
            message = "review clone sequence allocation source-marker injection"
            raise RuntimeError(message)
        injected = injected.replace(
            _SEQUENCE_ALLOCATION,
            "tuple ? clone_fault_tuple_new(size, source)"
            " : clone_fault_list_new(size, source)",
        )
    compiled_source = output / "adapter.c"
    compiled_source.write_text(injected)
    extension = native / ("_clone_native" + sysconfig.get_config_var("EXT_SUFFIX"))
    compiler = shlex.split(os.environ.get("CC") or sysconfig.get_config_var("CC"))
    executable = shutil.which(compiler[0])
    if executable is None:
        parser.error(f"compiler unavailable: {compiler[0]}")
    compiler[0] = executable
    command = [*compiler, "-shared", "-fPIC", "-O1", "-g"]
    if sys.platform == "darwin":
        command.extend(["-undefined", "dynamic_lookup"])
    command.extend(["-Wall", "-Wextra", "-Werror"])
    if arguments.force_gc:
        command.extend(
            [
                "-include",
                str(_ROOT / "tests/suites/product/clone/support/force_gc.h"),
            ]
        )
    if arguments.sanitize:
        command.extend(["-fsanitize=address,undefined", "-fno-omit-frame-pointer"])
    command.extend(
        [
            "-I" + sysconfig.get_path("include"),
            str(compiled_source),
            "-o",
            str(extension),
        ]
    )
    _run(command)

    bootstrap = output / "bootstrap"
    bootstrap.mkdir(exist_ok=True)
    wrapper = output / "python"
    (bootstrap / "sitecustomize.py").write_text(
        "import sys\n" + f"sys.executable = {str(wrapper)!r}\n"
    )
    environment = {
        "PYTHONPATH": os.pathsep.join(map(str, (source_root, bootstrap, _ROOT))),
        "PYTHONMALLOC": "malloc",
    }
    if arguments.sanitize:
        environment.update(
            {
                "DYLD_INSERT_LIBRARIES"
                if sys.platform == "darwin"
                else "LD_PRELOAD": str(_sanitizer_runtime(executable)),
                "ASAN_OPTIONS": "detect_leaks=0:halt_on_error=1:abort_on_error=1",
                "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
                "UNSTD_NATIVE_SANITIZERS": "1",
            }
        )
    assignments = " ".join(
        shlex.quote(f"{name}={value}") for name, value in environment.items()
    )
    wrapper.write_text(
        "#!/bin/sh\nexec env "
        + assignments
        + " "
        + shlex.quote(sys.executable)
        + ' "$@"\n'
    )
    wrapper.chmod(0o755)
    # The clean-env child deliberately drops PYTHONPATH and sanitizer variables;
    # the wrapper must restore both and import the identical extension.
    verification = f"""
import dataclasses, os, subprocess, sys
from unstd import clone
from unstd.clone import _clone_native, structural
assert _clone_native.__file__ == {str(extension)!r}, _clone_native.__file__
calls = []
original = structural._native_walk_graph
def observe(*args):
    result = original(*args)
    calls.append(result is not NotImplemented)
    return result
structural._native_walk_graph = observe
@dataclasses.dataclass
class Row:
    values: list
values = [1]
source = [Row(values), Row(values)]
actual = clone.deep(source)
assert calls == [True], calls
assert actual[0].values is actual[1].values is not values
child = subprocess.run([sys.executable, '-c',
    'from unstd.clone import _clone_native; print(_clone_native.__file__)'],
    env={{'PATH': os.environ.get('PATH', '')}}, check=True,
    text=True, capture_output=True)
assert child.stdout.strip() == {str(extension)!r}, child.stdout
"""
    _run([str(wrapper), "-c", verification], cwd=output)
    manifest = {
        "python": sys.version,
        "source_sha256": sha256(original).hexdigest(),
        "structural_sha256": sha256(
            (native / "structural.py").read_bytes()
        ).hexdigest(),
        "instrumented_source_sha256": sha256(compiled_source.read_bytes()).hexdigest(),
        "extension_sha256": sha256(extension.read_bytes()).hexdigest(),
        "builder_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "fault_header_sha256": (
            sha256(
                (_ROOT / "tests/suites/product/clone/support/force_gc.h").read_bytes()
            ).hexdigest()
            if arguments.force_gc
            else None
        ),
        "force_gc": arguments.force_gc,
        "sanitize": arguments.sanitize,
        "compiler_argv": command,
        "wrapper": str(wrapper),
        "public_native_route_verified": True,
        "clean_environment_child_origin_verified": True,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
