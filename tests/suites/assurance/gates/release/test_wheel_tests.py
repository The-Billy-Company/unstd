"""Parallel wheel contracts retain complete coverage and every shard failure."""

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests.support.environment.anchors import REPO_ROOT
from tests.tooling.release import wheel_tests


@pytest.mark.parametrize(
    ("available", "expected"), [(None, 1), (0, 1), (1, 1), (2, 2), (4, 4), (64, 4)]
)
def test_workers_are_bounded_by_available_cpus(monkeypatch, available, expected):
    monkeypatch.setattr(os, "process_cpu_count", lambda: available, raising=False)
    assert wheel_tests.worker_count() == expected


def test_requested_parallelism_cannot_exceed_available_cpus(monkeypatch):
    monkeypatch.setattr(os, "process_cpu_count", lambda: 2, raising=False)
    observed = []
    monkeypatch.setattr(
        wheel_tests,
        "run_tests",
        lambda project, *, workers: observed.append((project, workers)) or 0,
    )
    assert wheel_tests.main(["--workers", "4"]) == 0
    assert observed == [(REPO_ROOT, 2)]


@pytest.mark.parametrize("failures", [{"1": 1, "3": 5}, {"2": 5}])
def test_real_workers_all_start_keep_cwd_and_preserve_failures(
    tmp_path, monkeypatch, capsys, failures
):
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "pytest.py").write_text(
        textwrap.dedent(
            """\
            import json
            import os
            from pathlib import Path
            import sys
            import time

            index, count = sys.argv[sys.argv.index('--ci-shard') + 1].split('/')
            root = Path(os.environ['WHEEL_STUB_EVIDENCE'])
            (root / f'{index}.json').write_text(json.dumps({
                'arguments': sys.argv[1:], 'cwd': os.getcwd(),
            }))
            deadline = time.monotonic() + 10
            while len(list(root.glob('*.json'))) < int(count):
                if time.monotonic() >= deadline:
                    raise RuntimeError('workers did not start concurrently')
                time.sleep(0.01)
            print(f'stdout from {index}')
            print(f'stderr from {index}', file=sys.stderr)
            if index == '1':
                print('x' * 200000)
            raise SystemExit(json.loads(os.environ['WHEEL_STUB_FAILURES']).get(index, 0))
            """
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    monkeypatch.setenv("PYTHONPATH", str(stub))
    monkeypatch.setenv("WHEEL_STUB_EVIDENCE", str(evidence))
    monkeypatch.setenv("WHEEL_STUB_FAILURES", json.dumps(failures))
    project = tmp_path / "source"
    cwd = Path.cwd()
    assert wheel_tests.run_tests(project, workers=4) == next(iter(failures.values()))
    output = capsys.readouterr().out
    seen = []
    for index in range(1, 5):
        result = json.loads((evidence / f"{index}.json").read_text())
        assert result["cwd"] == str(cwd)
        arguments = result["arguments"]
        assert arguments[:5] == [
            str(project / "tests/suites/product" / name) for name in wheel_tests.MODULES
        ]
        assert arguments[arguments.index("--ci-shard") + 1] == f"{index}/4"
        seen.append(arguments[arguments.index("--basetemp") + 1])
        assert f"stdout from {index}" in output
        assert f"stderr from {index}" in output
        assert f"shard {index}/4 (exit {failures.get(str(index), 0)})" in output
    assert len(set(seen)) == 4
    assert "x" * 200000 in output


def test_real_pytest_shards_execute_every_contract_exactly_once(tmp_path, monkeypatch):
    project = tmp_path / "source"
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\naddopts = "--import-mode=importlib"\n',
        encoding="utf-8",
    )
    expected = set()
    for module in wheel_tests.MODULES:
        directory = project / "tests/suites/product" / module
        directory.mkdir(parents=True)
        source = "import json, os\nfrom pathlib import Path\n\n"
        for index in range(7):
            source += (
                f"def test_value_{index}(request):\n"
                "    path = Path(os.environ['WHEEL_TEST_EVIDENCE']) / f'{os.getpid()}.jsonl'\n"
                "    with path.open('a') as stream:\n"
                "        stream.write(json.dumps(request.node.nodeid) + '\\n')\n\n"
            )
            expected.add(
                f"tests/suites/product/{module}/test_contract.py::test_value_{index}"
            )
        (directory / "test_contract.py").write_text(source, encoding="utf-8")
    monkeypatch.setenv("WHEEL_TEST_EVIDENCE", str(evidence))
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    assert wheel_tests.run_tests(project, workers=4) == 0
    partitions = [
        [json.loads(line) for line in path.read_text().splitlines()]
        for path in evidence.glob("*.jsonl")
    ]
    assert len(partitions) == 4
    assert set.union(*map(set, partitions)) == expected
    assert sum(map(len, partitions)) == len(expected)
    assert max(map(len, partitions)) - min(map(len, partitions)) <= 1


@pytest.mark.parametrize("status", [0, 5])
def test_redirected_legacy_windows_output_preserves_bytes_and_status(tmp_path, status):
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "pytest.py").write_text(
        "import sys\n"
        "print('stdout ' + chr(233))\n"
        "print('stderr ' + chr(8364), file=sys.stderr)\n"
        f"raise SystemExit({status})\n",
        encoding="ascii",
    )
    source = (
        "import os, sys; from pathlib import Path; "
        "sys.path.insert(0, sys.argv[1]); "
        "from tests.tooling.release.wheel_tests import run_tests; "
        "os.environ['PYTHONPATH'] = sys.argv[2]; "
        "raise SystemExit(run_tests(Path('.'), workers=1))"
    )
    result = subprocess.run(  # noqa: S603 — fixed interpreter and repository runner
        [sys.executable, "-c", source, str(REPO_ROOT), str(stub)],
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == status, result.stderr.decode("cp1252")
    assert b"stdout \xe9" in result.stdout
    assert b"stderr \x80" in result.stdout
    assert b"\xef\xbf\xbd" not in result.stdout
    assert result.stderr == b""
