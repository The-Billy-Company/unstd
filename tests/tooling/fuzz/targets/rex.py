r"""Regex facade parity over an explicit shared grammar, plus hostile compilation.

Envelope: byte 0 modulo 2 selects generated execution or raw compilation; byte 1
selects grammar, flags and str/bytes transport. Remaining bytes supply the
subject (first 4,096 bytes), literal alternatives, replacement templates and
limits (mode 0), or a raw pattern followed by NUL and subject (mode 1). Raw
patterns are limited to 256 bytes and compile only: arbitrary backtracking regex
execution cannot be bounded by this in-process callback. The campaign worker
still bounds compile time and native faults.

The execution grammar includes native regular patterns, nullable expressions,
lookaround, backreferences and Unicode fallback cases. It deliberately uses
``\\Z`` instead of the documented native ``$`` divergence and excludes POSIX
classes. Generated literal fragments are escaped. All verbs, match captures,
spans, region bounds and substitution callback observations compare against re.
No native-code coverage instrumentation is implied by this Python harness.
"""

from __future__ import annotations

import re
import warnings

from unstd import rex


_PATTERNS = (
    r"[a-z]+",
    r"(a+)(b)?",
    r"(?P<word>\w+)",
    r"\d{1,4}",
    r"(?:ab|a)+",
    r"[^\r\n]+",
    r"\A[a-z]+\Z",
    r"a*",
    r"(?=a)",
    r"(a)\1",
    r"(?i:é)",
    r"\s+",
    r"((a)?)+",
    r"(?P<a>a)?b",
    r"a.*?b",
    r"\b[a-z]+\b",
)


def _match(value):
    if value is None:
        return None
    return (
        value.group(),
        value.groups(),
        value.groupdict(),
        tuple(value.span(i) for i in range(len(value.groups()) + 1)),
    )


def fuzz(data: bytes) -> None:
    """Exercise re-compatible results and the authoritative compile error type."""
    mode = data[0] % 2 if data else 0
    selector = data[1] if len(data) > 1 else 0
    payload = data[2:]
    flags = (0, re.ASCII, re.DOTALL, re.MULTILINE, re.IGNORECASE)[
        (selector // len(_PATTERNS)) % 5
    ]
    if mode == 1:
        raw = payload.partition(b"\0")[0][:256]
        pattern = raw.decode("utf-8", errors="surrogateescape")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            try:
                reference = re.compile(pattern, flags)
            except re.error as error:
                expected_error = type(error), error.msg, error.pos
                try:
                    rex.compile(pattern, flags)
                except re.error as actual:
                    actual_error = type(actual), actual.msg, actual.pos
                else:
                    msg = "regex facade accepted a pattern rejected by Python"
                    raise AssertionError(msg) from error
                assert actual_error == expected_error
            else:
                compiled = rex.compile(pattern, flags)
                assert compiled.pattern == reference.pattern
                assert compiled.flags == reference.flags
                assert compiled.groups == reference.groups
                assert compiled.groupindex == reference.groupindex
        return
    pattern = _PATTERNS[selector % len(_PATTERNS)]
    # Fixed controls ensure random bytes still exercise actual matches/captures.
    fragment = payload[:4096].decode("utf-8", errors="surrogateescape")
    subject = "aab ab 123 " + ("é\n" if selector & 32 else "\n") + fragment
    if selector & 64 and fragment:
        pattern = "(?:" + pattern + "|" + re.escape(fragment[:4]) + ")"
    if selector & 128:
        pattern = pattern.encode("utf-8", errors="surrogateescape")
        subject = subject.encode("utf-8", errors="surrogateescape")
    limit = (-1, 0, 1, 3)[payload[0] % 4 if payload else selector % 4]
    reference = re.compile(pattern, flags)
    compiled = rex.compile(pattern, flags)
    assert compiled.pattern == reference.pattern
    assert compiled.flags == reference.flags
    for verb in ("search", "match", "fullmatch"):
        assert _match(getattr(rex, verb)(pattern, subject, flags)) == _match(
            getattr(re, verb)(pattern, subject, flags)
        )
        assert _match(getattr(compiled, verb)(subject, 1, len(subject) - 1)) == _match(
            getattr(reference, verb)(subject, 1, len(subject) - 1)
        )
    assert rex.findall(pattern, subject, flags) == reference.findall(subject)
    assert [_match(value) for value in rex.finditer(pattern, subject, flags)] == [
        _match(value) for value in reference.finditer(subject)
    ]
    assert rex.split(pattern, subject, limit, flags) == reference.split(subject, limit)
    replacement = ("X", r"\g<0>", r"[\g<0>]")[payload[1] % 3 if len(payload) > 1 else 0]
    if isinstance(subject, bytes):
        replacement = replacement.encode()
    assert rex.sub(pattern, replacement, subject, limit, flags) == reference.sub(
        replacement, subject, limit
    )
    assert rex.subn(pattern, replacement, subject, limit, flags) == reference.subn(
        replacement, subject, limit
    )
    observed = []
    expected_observed = []

    def replace(match):
        observed.append(_match(match))
        return replacement

    def reference_replace(match):
        expected_observed.append(_match(match))
        return replacement

    assert compiled.sub(replace, subject, limit) == reference.sub(
        reference_replace, subject, limit
    )
    assert observed == expected_observed
