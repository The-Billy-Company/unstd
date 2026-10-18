"""A resource-bounded pytest worker that journals each chaos case before exit."""

from __future__ import annotations

import argparse
import contextlib
from importlib import metadata
import json
import os
from pathlib import Path
import signal
import sys
import time


REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / "src"), str(REPO)]


def _write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=True) + "\n")
    temporary.replace(path)


def _backend_evidence():
    from tests.support.chaos import BLOCKED_BACKENDS

    distributions = {
        "uuid_utils": "uuid-utils",
        "zlib_ng": "zlib-ng",
        "ruamel": "ruamel-yaml",
        "irgx": "irregex",
    }
    modules = {
        prefix: sorted(
            name
            for name in sys.modules
            if name == prefix or name.startswith(prefix + ".")
        )
        for prefix in BLOCKED_BACKENDS
        if any(name == prefix or name.startswith(prefix + ".") for name in sys.modules)
    }
    versions = {}
    for prefix in modules:
        try:
            versions[prefix] = metadata.version(distributions.get(prefix, prefix))
        except metadata.PackageNotFoundError:
            versions[prefix] = None
    facades = {}
    for name, module in tuple(sys.modules.items()):
        if not name.startswith("unstd.") or module is None:
            continue
        flags = {
            key: value
            for key, value in vars(module).items()
            if key.startswith(("HAVE_", "_HAVE_")) and type(value) is bool
        }
        if "_backend" in vars(module):
            value = module._backend
            flags["_backend"] = getattr(value, "__name__", None)
        for function in ("_uuid7", "token_bytes", "time_ns", "perf_counter"):
            if function in vars(module):
                flags[function] = getattr(vars(module)[function], "__module__", None)
        if flags:
            facades[name] = flags
    return {
        "backend_modules": modules,
        "backend_versions": versions,
        "facade_bindings": facades,
    }


class Reporter:
    """Retain every terminal result and the currently executing test identity."""

    def __init__(self, directory):
        self.directory = directory
        self.records = directory / "cases.jsonl"
        self.records.write_text("")
        self.sequence = 0
        self.collected = 0

    def pytest_configure(self, config):
        config._unstd_chaos_journal_directory = self.directory / "journals"

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)
        _write(
            self.directory / "collection.json",
            {
                "count": self.collected,
                "nodeids": [item.nodeid for item in session.items],
            },
        )

    def pytest_runtest_logstart(self, nodeid, location):
        self.sequence += 1
        _write(
            self.directory / "active.json",
            {
                "sequence": self.sequence,
                "nodeid": nodeid,
                "started_monotonic": time.monotonic(),
            },
        )

    def pytest_runtest_logreport(self, report):
        row = {
            "nodeid": report.nodeid,
            "phase": report.when,
            "outcome": report.outcome,
            "duration_seconds": report.duration,
            "properties": dict(report.user_properties),
        }
        if report.failed or report.skipped:
            row["diagnostic"] = str(report.longrepr)[-8192:]
        encoded = json.dumps(row, ensure_ascii=True) + "\n"
        if self.records.stat().st_size + len(encoded.encode()) > 16 * 1024 * 1024:
            message = "chaos evidence exceeds 16 MiB budget"
            raise RuntimeError(message)
        with self.records.open("a", encoding="utf-8") as stream:
            stream.write(encoded)

    def pytest_runtest_logfinish(self, nodeid, location):
        _write(
            self.directory / "active.json", {"sequence": self.sequence, "nodeid": None}
        )


def main():
    """Apply isolation before imports and never interpret a partial run as success."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--backend", choices=("installed", "stdlib"), required=True)
    parser.add_argument("--cpu-seconds", type=int, required=True)
    parser.add_argument("--memory-mb", type=int, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    enforcement = {
        "cpu": False,
        "address_space": False,
        "core_dumps_disabled": False,
        "process_group": os.name == "posix",
    }
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(
            resource.RLIMIT_CPU, (args.cpu_seconds, args.cpu_seconds + 1)
        )
        enforcement.update(cpu=True, core_dumps_disabled=True)
        if sys.platform.startswith("linux"):
            memory = args.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            enforcement["address_space"] = True
    os.environ.update(
        PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        UNSTD_CHAOS_BACKEND=args.backend,
        UNSTD_CHAOS_OWNED_GROUP="1",
        OPENBLAS_NUM_THREADS="1",
        OMP_NUM_THREADS="1",
    )
    from tests.support.chaos import apply_backend_policy

    apply_backend_policy()
    _write(
        args.directory / "admission.json",
        {
            "schema_version": 1,
            "backend": args.backend,
            "seed": args.seed,
            "enforcement": enforcement,
            **_backend_evidence(),
        },
    )
    import pytest

    reporter = Reporter(args.directory)
    arguments = [
        args.path,
        "-q",
        "-p",
        "no:cacheprovider",
        "-m",
        "chaos",
        "--chaos-seed",
        str(args.seed),
        "--basetemp",
        str(args.directory / "temporary"),
    ]
    if args.backend == "installed":
        arguments.append("--require-extras")
    # The outer process owns every descendant in this group. Tests' local
    # supervisors reap their children first; its final kill catches leftovers.
    if os.name == "posix":
        signal.signal(signal.SIGTERM, signal.default_int_handler)
    with contextlib.redirect_stdout(sys.stderr):
        code = int(pytest.main(arguments, plugins=[reporter]))
    _write(
        args.directory / "complete.json",
        {
            "exitcode": code,
            "collected": reporter.collected,
            "enforcement": enforcement,
            **_backend_evidence(),
        },
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
