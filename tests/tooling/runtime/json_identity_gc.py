"""Build an isolated identity-snapshot kernel with forced collection points.

Run with the intended Python interpreter, for example::

    python -m tests.tooling.runtime.json_identity_gc --result /tmp/identity-gc.json

Production sources are copied, never edited. The audit injects collection both
before allocations and after source/output ownership transfers. Update the
explicit injection points whenever the snapshot implementation changes.
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
import xml.etree.ElementTree as ET


_ROOT = Path(__file__).resolve().parents[3]
_BEFORE = (
    "iterator = PyObject_GetIter(source);",
    "output = mapping ? PyDict_New() : PyList_New(0);",
    "view = PyObject_CallOneArg(context->items_method, source);",
    "iterator = PyObject_GetIter(view);",
    "status = identity_snapshot_value(current, context, depth + 1, &copied);",
    "context.outputs = PyList_New(0);",
    'PyObject *items_name = PyUnicode_InternFromString("items");',
    "context.items_method = PyObject_GetAttr((PyObject *)&PyDict_Type, items_name);",
    "admission.snapshot = PyDict_New();",
)

_FAILURE_SUPPORT = r"""
/* Isolated audit only: fault one owned allocation/insertion, then disarm. */
static Py_ssize_t identity_audit_remaining = -1;

static void identity_audit_collect(void)
{
    if (identity_audit_remaining < 0) PyGC_Collect();
}

static int identity_audit_failure(void)
{
    if (identity_audit_remaining < 0) return 0;
    if (identity_audit_remaining-- != 0) return 0;
    identity_audit_remaining = -1;
    /* Observe privacy exactly at the failed boundary, before raising. */
    PyGC_Collect();
    PyErr_NoMemory();
    return 1;
}

static PyObject *identity_audit_fail_after(PyObject *module, PyObject *value)
{
    (void)module;
    if (!PyLong_CheckExact(value)) {
        PyErr_SetString(PyExc_TypeError, "failure position must be an integer");
        return NULL;
    }
    Py_ssize_t position = PyLong_AsSsize_t(value);
    if (position == -1 && PyErr_Occurred()) return NULL;
    if (position < -1) {
        PyErr_SetString(PyExc_ValueError, "failure position must be >= -1");
        return NULL;
    }
    identity_audit_remaining = position;
    Py_RETURN_NONE;
}

static PyObject *identity_audit_list_new(Py_ssize_t size)
{return identity_audit_failure() ? NULL : PyList_New(size);}
static PyObject *identity_audit_dict_new(void)
{return identity_audit_failure() ? NULL : PyDict_New();}
static PyObject *identity_audit_getiter(PyObject *value)
{return identity_audit_failure() ? NULL : PyObject_GetIter(value);}
static PyObject *identity_audit_callone(PyObject *callable, PyObject *value)
{return identity_audit_failure() ? NULL : PyObject_CallOneArg(callable, value);}
static PyObject *identity_audit_getattr(PyObject *value, const char *name)
{return identity_audit_failure() ? NULL : PyObject_GetAttrString(value, name);}
static PyObject *identity_audit_intern(const char *name)
{return identity_audit_failure() ? NULL : PyUnicode_InternFromString(name);}
static PyObject *identity_audit_getattr_object(PyObject *value, PyObject *name)
{return identity_audit_failure() ? NULL : PyObject_GetAttr(value, name);}
static int identity_audit_append(PyObject *list, PyObject *value)
{return identity_audit_failure() ? -1 : PyList_Append(list, value);}
static int identity_audit_setitem(PyObject *dict, PyObject *key, PyObject *value)
{return identity_audit_failure() ? -1 : PyDict_SetItem(dict, key, value);}
"""


def _run(arguments, **kwargs):
    return subprocess.run(  # noqa: S603 - explicit tool/interpreter argv, no shell
        arguments, check=True, timeout=120, **kwargs
    )


def _hashes(directory, patterns):
    return {
        str(path.relative_to(directory)): sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and any(path.match(pattern) for pattern in patterns)
    }


def _inject(source):
    for statement in _BEFORE:
        if source.count(statement) != 1:
            message = f"update identity GC injection point: {statement}"
            raise ValueError(message)
        source = source.replace(
            statement, "identity_audit_collect();\n    " + statement
        )
    statement = "    for (;;) {"
    if source.count(statement) != 1:
        message = "update identity GC iterator/output ownership injection point"
        raise ValueError(message)
    source = source.replace(statement, "    identity_audit_collect();\n" + statement)
    statement = "        Py_CLEAR(copied);"
    if source.count(statement) != 1:
        message = "update identity GC inserted-edge ownership injection point"
        raise ValueError(message)
    source = source.replace(
        statement,
        statement + "\n        if (inserted == 0) identity_audit_collect();",
    )
    for original, replacement in (
        ("PyList_New(", "identity_audit_list_new("),
        ("PyDict_New(", "identity_audit_dict_new("),
        ("PyObject_GetIter(", "identity_audit_getiter("),
        ("PyObject_CallOneArg(", "identity_audit_callone("),
        ("PyObject_GetAttrString(", "identity_audit_getattr("),
        ("PyUnicode_InternFromString(", "identity_audit_intern("),
        ("PyObject_GetAttr(", "identity_audit_getattr_object("),
        ("PyList_Append(", "identity_audit_append("),
        ("PyDict_SetItem(", "identity_audit_setitem("),
    ):
        source = source.replace(original, replacement)
    return _FAILURE_SUPPORT + source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_ROOT / "src")
    parser.add_argument("--native-dir", type=Path, default=_ROOT / "native/serde/src")
    parser.add_argument("--result", type=Path)
    arguments = parser.parse_args()
    build = Path(mkdtemp(prefix="jsonx-identity-gc-"))
    shutil.copytree(
        arguments.source,
        build / "src",
        ignore=shutil.ignore_patterns("*.so", "*.pyd", "__pycache__"),
    )
    shutil.copytree(arguments.native_dir, build / "native")
    original_native = _hashes(build / "native", ("*.c", "*.h"))
    header = build / "native/identity.h"
    if not header.exists():
        # Archived prototypes predate the production source rename.
        header = build / "native/_json_identity_native.h"
    original = header.read_bytes()
    header.write_text(_inject(original.decode()))
    compiler = shlex.split(
        os.environ.get("CC", "cc" if sys.platform == "darwin" else "gcc")
    )
    resolved = shutil.which(compiler[0])
    if resolved is None:
        parser.error(f"compiler unavailable: {compiler[0]}")
    compiler[0] = resolved
    flags = [
        "-O1",
        "-g",
        "-fPIC",
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
        "-fno-sanitize-recover=all",
    ]
    if sys.platform == "darwin":
        sdk = _run(
            ["xcrun", "--show-sdk-path"], capture_output=True, text=True
        ).stdout.strip()
        flags.extend(["-isysroot", sdk, "-bundle", "-undefined", "dynamic_lookup"])
        resource = _run(
            [*compiler, "-print-resource-dir"], capture_output=True, text=True
        ).stdout.strip()
        runtime = str(Path(resource) / "lib/darwin/libclang_rt.asan_osx_dynamic.dylib")
        preload = "DYLD_INSERT_LIBRARIES"
    elif sys.platform == "linux":
        flags.append("-shared")
        runtime = _run(
            [*compiler, "-print-file-name=libasan.so"], capture_output=True, text=True
        ).stdout.strip()
        preload = "LD_PRELOAD"
    else:
        parser.error("this isolated sanitizer runner supports macOS and Linux")
    library = (
        build
        / "src/unstd/serde"
        / ("_json_native" + sysconfig.get_config_var("EXT_SUFFIX"))
    )
    adapter = build / "native/adapter.c"
    if not adapter.exists():
        adapter = build / "native/_json_native.c"
    adapter_source = adapter.read_text()
    marker = "static PyMethodDef methods[] = {"
    if adapter_source.count(marker) != 1:
        message = "update identity audit-only failure method registration"
        raise ValueError(message)
    adapter.write_text(
        adapter_source.replace(
            marker,
            marker
            + '\n    {"_identity_fail_after", identity_audit_fail_after, METH_O, "Audit-only failure injection."},',
        )
    )
    command = [
        *compiler,
        *flags,
        "-I" + sysconfig.get_path("include"),
        str(adapter),
        "-o",
        str(library),
    ]
    # Separate compilation from linking: retain DWARF in the object without
    # launching macOS dsymutil, whose CoreFoundation startup can block in CI.
    object_file = build / "identity-audit.o"
    compile_command = [
        *compiler,
        *(
            flag
            for flag in flags
            if flag not in ("-bundle", "-shared", "-undefined", "dynamic_lookup")
        ),
        "-I" + sysconfig.get_path("include"),
        "-c",
        str(adapter),
        "-o",
        str(object_file),
    ]
    link_command = [
        *compiler,
        *(flag for flag in flags if flag != "-g"),
        str(object_file),
        "-o",
        str(library),
    ]
    _run(compile_command)
    _run(link_command)
    environment = {
        **os.environ,
        preload: runtime,
        "ASAN_OPTIONS": "detect_leaks=0:abort_on_error=1:detect_stack_use_after_return=1",
        "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
        "PYTHONMALLOC": "malloc",
        "UNSTD_JSONX_IDENTITY_GC_STRESS": "1",
        "PYTHONPATH": os.pathsep.join((str(build / "src"), str(_ROOT))),
    }
    script = (
        "import os,sys;"
        f"os.environ[{preload!r}]={runtime!r};"
        "from unstd.serde import _json_native as native;"
        f"assert native.__file__=={str(library)!r};"
        "assert hasattr(native,'identity_snapshot');"
        "import pytest;raise SystemExit(pytest.main(sys.argv[1:]))"
    )
    invocation = [
        sys.executable,
        "-c",
        script,
        str(_ROOT / "tests/suites/product/serde/parity/test_jsonx_identity_native.py"),
        "-q",
        f"--junitxml={build / 'results.xml'}",
    ]
    result = subprocess.run(  # noqa: S603 - selected interpreter and fixed test file
        invocation,
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    evidence = {
        "python": sys.version,
        "platform": platform.platform(),
        "compiler": _run(
            [*compiler, "--version"], capture_output=True, text=True
        ).stdout,
        "compiler_command": command,
        "executed_build_commands": [compile_command, link_command],
        "runtime": {
            name: environment[name]
            for name in (preload, "ASAN_OPTIONS", "UBSAN_OPTIONS", "PYTHONMALLOC")
        },
        "original_identity_header_sha256": sha256(original).hexdigest(),
        "original_native_sources_sha256": original_native,
        "injected_native_sources_sha256": _hashes(build / "native", ("*.c", "*.h")),
        "serde_python_sources_sha256": _hashes(build / "src/unstd/serde", ("*.py",)),
        "audit_runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "retained_test_sha256": sha256(
            (
                _ROOT
                / "tests/suites/product/serde/parity/test_jsonx_identity_native.py"
            ).read_bytes()
        ).hexdigest(),
        "injected_identity_header_sha256": sha256(header.read_bytes()).hexdigest(),
        "loaded_library_sha256": sha256(library.read_bytes()).hexdigest(),
        "command": invocation,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "scope": "Only the copied JSONX C extension is instrumented; CPython and third-party libraries are not rebuilt.",
    }
    if (build / "results.xml").exists():
        # This XML is emitted by the fixed local pytest invocation above.
        suites = ET.parse(build / "results.xml").getroot()  # noqa: S314
        evidence["test_counts"] = {
            key: sum(int(suite.get(key, "0")) for suite in suites.iter("testsuite"))
            for key in ("tests", "failures", "errors", "skipped")
        }
    destination = arguments.result or build / "result.json"
    destination.write_text(json.dumps(evidence, indent=2) + "\n")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    print(destination)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
