"""Run instrumented main tests and instrumented owned children separately.

Some GCC ASan/ARM64 runtimes trap in forked Python children before exec, even
for a child that only prints a number. The main phase loads ASan in pytest;
the children phase uses an ordinary launcher and loads ASan only after exec.
Both phases keep the real owned-process helper and its deadlines/group cleanup.
No product code, child working directory, or isolation contract is replaced.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest


class _Selection:
    """Partition direct owned-worker tests from tests in this interpreter."""

    def __init__(self, *, children: bool) -> None:
        self.children = children

    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        """Retain each test in exactly one sanitizer phase."""
        kept, removed = [], []
        for item in items:
            code = getattr(getattr(item, "obj", None), "__code__", None)
            owns_child = code is not None and "run_python" in code.co_names
            (kept if owns_child == self.children else removed).append(item)
        config.hook.pytest_deselected(items=removed)
        items[:] = kept


def main(argv: Sequence[str] | None = None) -> int:
    """Run one complete partition against the selected native build."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--sanitized-source", type=Path, required=True)
    parser.add_argument("--asan-library", type=Path, required=True)
    parser.add_argument("--phase", choices=("main", "children"), required=True)
    args, pytest_args = parser.parse_known_args(argv)
    source = args.source.resolve()
    sanitized = args.sanitized_source.resolve()
    children = args.phase == "children"
    if children and os.environ.get("LD_PRELOAD"):
        parser.error("the children launcher must start without LD_PRELOAD")
    if not children and not os.environ.get("LD_PRELOAD"):
        parser.error("the main phase requires the sanitizer runtime preloaded")
    sys.path[:0] = [str(source / "src"), str(source)]

    import pytest

    from tests.support.environment import process
    from unstd.containers import _sorted_native

    expected = source / "src/unstd/containers"
    if Path(_sorted_native.__file__).resolve().parent != expected:
        parser.error("the parent imported a native module outside its selected source")
    routed = 0
    if children:
        original = process.python_argv

        def routed_argv(code: str, arguments: Sequence[str] = ()) -> list[str]:
            nonlocal routed
            routed += 1
            prelude = f"import sys; sys.path.insert(0, {str(sanitized / 'src')!r});\n"
            return original(prelude + code, arguments)

        process.python_argv = routed_argv
        # Setting the environment after process startup does not load ASan in
        # this launcher. Each owned child loads it during its ordinary exec.
        os.environ["LD_PRELOAD"] = str(args.asan_library.resolve())
    os.environ["ASAN_OPTIONS"] = "detect_leaks=0:abort_on_error=1"
    os.environ["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
    os.environ["PYTHONMALLOC"] = "malloc"
    os.environ["PYTHONFAULTHANDLER"] = "1"
    os.environ["UNSTD_NATIVE_SANITIZERS"] = "1"
    result = pytest.main(
        [str(source / "tests/suites/product/containers"), *pytest_args],
        plugins=[_Selection(children=children)],
    )
    if children and routed == 0:
        parser.error("no owned sanitizer children ran")
    print(json.dumps({"phase": args.phase, "sanitized_children": routed}))
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
