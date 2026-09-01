"""Adversarial tests for the safe subprocess runner (``unstd.proc``).

These pin the three guardrails the module exists to provide — no shell (a
metacharacter argument is passed literally, never interpreted), a bounded
timeout (a sleep past the deadline is killed and raises), and lossless failures
(a non-zero exit raises carrying the child's stderr) — plus the ergonomic
convenience surface (``run_ok`` bool, ``capture`` strip/decode). Commands are
trivial and portable: everything runs through ``sys.executable -c`` so no
assumption about ``/bin`` layout leaks in.
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from unstd import proc


if TYPE_CHECKING:
    from collections.abc import Mapping


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


# ── shell=False: metacharacters are inert, bare strings are refused ────────────


def test_no_shell_metachars_are_passed_literally_not_interpreted() -> None:
    # A shell would run the substitution and yield "INJECTED"; with shell=False the
    # token reaches the program verbatim. Equality-to-literal + inequality-to-
    # substituted-result together prove no shell interpolation happened.
    """Test no shell metachars are passed literally not interpreted."""
    payload = "$(echo INJECTED) ; rm -rf / `id`"
    r = proc.run([*_py("import sys; print(sys.argv[1])"), payload])
    assert r.stdout.strip() == payload
    assert r.stdout.strip() != "INJECTED"


def test_bare_string_argv_is_refused() -> None:
    """Bare ``str`` argv is refused with the no-shell contract message."""
    with pytest.raises(TypeError, match="refuses a bare string"):
        proc.run(
            "echo hi"
        )  # a str IS a Sequence[str] statically — only runtime refuses it


# ── timeout: a call can never hang forever ─────────────────────────────────────


def test_timeout_fires_on_a_sleep_longer_than_the_deadline() -> None:
    """TimeoutExpired carries the caller-supplied deadline (child was killed)."""
    with pytest.raises(proc.TimeoutExpired) as ei:
        proc.run(_py("import time; time.sleep(30)"), timeout=0.25)
    assert ei.value.timeout == 0.25


def test_timeout_alias_is_the_subprocess_class() -> None:
    """Test timeout alias is the subprocess class."""
    assert proc.TimeoutError is subprocess.TimeoutExpired
    with pytest.raises(proc.TimeoutError):
        proc.run(_py("import time; time.sleep(30)"), timeout=0.25)


# ── check=True: non-zero exit raises, and keeps the stderr ─────────────────────


def test_nonzero_exit_raises_with_captured_stderr_attached() -> None:
    """Test nonzero exit raises with captured stderr attached."""
    with pytest.raises(proc.ProcessError) as ei:
        proc.run(_py("import sys; sys.stderr.write('boom-detail'); sys.exit(3)"))
    err = ei.value
    assert err.returncode == 3
    assert "boom-detail" in err.stderr
    assert "boom-detail" in str(err)  # stderr folded into the message, not lost


def test_process_error_is_a_faithful_calledprocesserror_subclass() -> None:
    # Callers migrating off subprocess keep their existing except clause.
    """ProcessError subclasses CalledProcessError and preserves returncode."""
    with pytest.raises(subprocess.CalledProcessError) as ei:
        proc.run(_py("import sys; sys.exit(1)"))
    assert ei.value.returncode == 1
    assert isinstance(ei.value, proc.ProcessError)
    with pytest.raises(proc.CalledProcessError) as ei2:
        proc.run(_py("import sys; sys.exit(1)"))
    assert ei2.value.returncode == 1


def test_check_false_returns_result_instead_of_raising() -> None:
    """Test check false returns result instead of raising."""
    r = proc.run(_py("import sys; sys.exit(7)"), check=False)
    assert r.returncode == 7
    assert r.ok is False


# ── run_ok: a clean boolean predicate ──────────────────────────────────────────


def test_run_ok_true_on_success_false_on_failure() -> None:
    """Test run ok true on success false on failure."""
    assert proc.run_ok(_py("pass")) is True
    assert proc.run_ok(_py("import sys; sys.exit(1)")) is False


def test_run_ok_is_false_on_timeout_never_raises() -> None:
    """Test run ok is false on timeout never raises."""
    assert proc.run_ok(_py("import time; time.sleep(30)"), timeout=0.25) is False


# ── capture: decoded stdout, stripped ──────────────────────────────────────────


def test_capture_strips_and_decodes_stdout() -> None:
    """Test capture strips and decodes stdout."""
    assert proc.capture(_py("print('  hello world  ')")) == "hello world"


def test_capture_decodes_utf8() -> None:
    """Test capture decodes utf8."""
    assert proc.capture(_py("print('café — 日本語')")) == "café — 日本語"


def test_capture_replaces_undecodable_bytes_without_raising() -> None:
    # Invalid UTF-8 on stdout must not blow up — errors="replace" is the default.
    """Test capture replaces undecodable bytes without raising."""
    out = proc.capture(_py(r"import sys; sys.stdout.buffer.write(b'\xff\xfe\xfa')"))
    assert "\ufffd" in out


# ── result type: frozen, self-describing ───────────────────────────────────────


def test_completed_process_fields_and_immutability() -> None:
    """Test completed process fields and immutability."""
    r = proc.run(_py("print('x')"))
    assert r.argv[0] == sys.executable
    assert r.returncode == 0
    assert r.stdout.strip() == "x"
    assert r.stderr == ""
    assert r.duration >= 0.0
    assert r.ok is True
    with pytest.raises(AttributeError):  # frozen dataclass — mutation must be refused
        setattr(r, "returncode", 9)  # noqa: B010 — attribute write must stay dynamic to compile


# ── log hook: injectable, structured, stdlib-only ──────────────────────────────


def test_log_hook_receives_one_structured_record() -> None:
    """Test log hook receives one structured record."""
    events: list[Mapping[str, object]] = []
    proc.run(_py("pass"), log=events.append)
    assert len(events) == 1
    rec = events[0]
    assert rec["event"] == "proc.run"
    assert rec["returncode"] == 0
    argv = rec["argv"]
    assert isinstance(argv, tuple)
    assert argv[0] == sys.executable
    assert isinstance(rec["duration"], float)
