"""Native envelope parsing retains Python hooks and owns no request secrets.

These contracts cover the C/Python boundary, independently of the canonical
envelope and constant-time primitive tests shared by both implementations.
"""

from __future__ import annotations

import gc
import inspect
import signal
import subprocess
import sys
from types import FunctionType, SimpleNamespace
import weakref

import pytest


token = pytest.importorskip("unstd.crypto.token", exc_type=ImportError)
_KEY = bytes(range(32))
_CLAIMS = b"native token boundary fixture"


@pytest.fixture
def native_module():
    return pytest.importorskip("unstd.crypto._stream_native")


@pytest.fixture(params=["python", "native"])
def verify(request):
    if request.param == "python":
        return token._verify_python
    native_module = request.getfixturevalue("native_module")
    helper = native_module.bind_token_verifier(token._verify_python)

    def accelerated(key, bearer, *, max_token_chars=8192):
        return helper(key, bearer, max_token_chars)

    return accelerated


def test_public_accelerator_preserves_signature_and_metadata(native_module):
    if token.verify is not token._verify_python:
        assert token._native_verify.__self__[0] is token.__dict__
        assert token._native_verify.__self__[1] is token._verify_python
    assert inspect.signature(token.verify) == inspect.signature(token._verify_python)
    assert token.verify.__name__ == token.verify.__qualname__ == "verify"
    assert token.verify.__doc__ == token._verify_python.__doc__
    assert "verify" in token.__all__
    assert "prepare_verifier" in token.__all__
    assert "bind_token_verifier" not in token.__all__


@pytest.mark.parametrize("digest_first", [False, True])
def test_import_order_never_adds_an_implicit_native_load(native_module, digest_first):
    script = """
import sys
digest_first = DIGEST_FIRST
if digest_first:
    from unstd.crypto import digest
    loaded = sys.modules['unstd.crypto._stream_native']
from unstd.crypto import token
if digest_first:
    assert sys.modules['unstd.crypto._stream_native'] is loaded
    assert token.verify is not token._verify_python
    assert token._native_verify.__self__[0] is token.__dict__
    assert token._native_verify.__self__[1] is token._verify_python
else:
    assert 'unstd.crypto._stream_native' not in sys.modules
    assert token.verify is token._verify_python
before = token.verify
prepared = token.prepare_verifier()
assert 'unstd.crypto._stream_native' in sys.modules
assert token.verify is before
key = bytes(range(32))
assert prepared(key=key, token=token.mint(key, b'prepared')) == b'prepared'
assert prepared(key, 'invalid') is None
""".replace("DIGEST_FIRST", repr(digest_first))
    result = subprocess.run(  # noqa: S603  # explicit interpreter and fixed fixture
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_native_extension_selects_python_once_at_import():
    script = """
import inspect
import sys
sys.modules['unstd.crypto._stream_native'] = None
from unstd.crypto import token
assert token.verify is token._verify_python
assert token.verify.__name__ == token.verify.__qualname__ == 'verify'
assert tuple(inspect.signature(token.verify).parameters) == ('key', 'token', 'max_token_chars')
key = bytes(range(32))
assert token.verify(key=key, token=token.mint(key, b'portable')) == b'portable'
assert token.verify(key, 'invalid') is None
assert token.verify(key, token.mint(key, b'x' * 7000)) is None
assert token.verify(key, token.mint(key, b'x' * 7000), max_token_chars=None) == b'x' * 7000
prepared = token.prepare_verifier()
assert token.verify is token._verify_python
assert prepared(key=key, token=token.mint(key, b'portable')) == b'portable'
assert prepared(key, token.mint(key, b'x' * 7000)) is None
assert token.prepare_verifier(max_token_chars=None)(key, token.mint(key, b'x' * 7000)) == b'x' * 7000
"""
    result = subprocess.run(  # noqa: S603  # explicit interpreter and fixed fixture
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("limit", [None, 1, 8192, 1 << 10000])
def test_prepared_policy_is_fixed_and_never_reprobes_the_backend(monkeypatch, limit):
    prepared = token.prepare_verifier(max_token_chars=limit)
    bearer = token.mint(_KEY, _CLAIMS)
    expected = _CLAIMS if limit is None or limit >= len(bearer) else None
    # An import blocker after setup catches per-request backend selection.
    monkeypatch.setitem(sys.modules, "unstd.crypto._stream_native", None)
    for _ in range(3):
        assert prepared(_KEY, bearer) == expected
        assert prepared(_KEY, "invalid") is None
    assert tuple(inspect.signature(prepared).parameters) == ("key", "token")


@pytest.mark.parametrize("limit", [True, False, 8192.0, "8192", b"8192", object()])
def test_prepared_noninteger_policy_fails_before_optional_import(monkeypatch, limit):
    monkeypatch.setitem(sys.modules, "unstd.crypto._stream_native", None)
    with pytest.raises(TypeError, match="positive integer or None"):
        token.prepare_verifier(max_token_chars=limit)


@pytest.mark.parametrize("limit", [0, -1, -(1 << 10000)])
def test_prepared_nonpositive_policy_fails_before_optional_import(monkeypatch, limit):
    monkeypatch.setitem(sys.modules, "unstd.crypto._stream_native", None)
    with pytest.raises(ValueError, match="positive integer or None"):
        token.prepare_verifier(max_token_chars=limit)


def test_prepared_integer_subclass_policy_is_rejected():
    class Limit(int):
        pass

    with pytest.raises(TypeError, match="positive integer or None"):
        token.prepare_verifier(max_token_chars=Limit(8192))


def test_native_binding_rejects_nonfunctions_and_wrong_arity(native_module):
    with pytest.raises(TypeError, match="Python fallback function"):
        native_module.bind_token_verifier(object())
    helper = native_module.bind_token_verifier(token._verify_python)
    for args in [(), (_KEY,), (_KEY, ""), (_KEY, "", 8192, None)]:
        with pytest.raises(TypeError, match="exactly three arguments"):
            helper(*args)
    with pytest.raises(TypeError):
        helper(_KEY, "", max_token_chars=8192)


def test_callbacks_can_replace_each_other_and_collect_during_verification(
    verify, monkeypatch
):
    bearer = token.mint(_KEY, _CLAIMS)
    original = token.b64
    original_mac, original_equal = token.mac_raw, token.equal
    events = []

    def decode_tag(value, *, url):
        events.append(("tag", value))
        monkeypatch.setattr(token, "mac_raw", mac)
        monkeypatch.setattr(token, "equal", compare)
        gc.collect()
        return original.decode(value, url=url)

    def mac(key, value):
        events.append(("mac", value))
        gc.collect()
        return original_mac(key, value)

    def compare(left, right):
        events.append(("compare", left, right))
        monkeypatch.setattr(token, "b64", SimpleNamespace(decode=decode_claims))
        gc.collect()
        return original_equal(left, right)

    def decode_claims(value, *, url):
        events.append(("claims", value))
        return original.decode(value, url=url)

    monkeypatch.setattr(token, "b64", SimpleNamespace(decode=decode_tag))
    assert verify(_KEY, bearer) == _CLAIMS
    assert [event[0] for event in events] == ["tag", "mac", "compare", "claims"]
    payload, signature = bearer.split(".")
    assert events[0][1] == signature
    assert events[1][1] == events[3][1] == payload.encode("ascii")
    assert len(events[2][1]) == len(events[2][2]) == 32


@pytest.mark.parametrize("stage", ["tag", "mac", "equal", "truth", "claims"])
@pytest.mark.parametrize("error", [ValueError, RuntimeError, KeyboardInterrupt])
def test_callback_exceptions_keep_the_python_error_boundary(
    verify, monkeypatch, stage, error
):
    bearer = token.mint(_KEY, _CLAIMS)
    decode = token.b64.decode
    failure = error("callback failure")
    count = 0

    def refuse(*_args, **_kwargs):
        raise failure

    def decode_at_stage(value, *, url):
        nonlocal count
        count += 1
        if (stage == "tag" and count == 1) or (stage == "claims" and count == 2):
            raise failure
        return decode(value, url=url)

    class Truth:
        def __bool__(self):
            raise failure

    monkeypatch.setattr(token, "b64", SimpleNamespace(decode=decode_at_stage))
    if stage == "mac":
        monkeypatch.setattr(token, "mac_raw", refuse)
    elif stage == "equal":
        monkeypatch.setattr(token, "equal", refuse)
    elif stage == "truth":
        monkeypatch.setattr(token, "equal", lambda *_args: Truth())
    if stage == "tag" and error is ValueError:
        assert verify(_KEY, bearer) is None
    else:
        with pytest.raises(error, match="callback failure") as caught:
            verify(_KEY, bearer)
        assert caught.value is failure


@pytest.mark.parametrize("name", ["b64", "mac_raw", "equal"])
def test_missing_module_callbacks_raise_name_error(native_module, name):
    namespace = dict(token.__dict__)
    fallback = FunctionType(token._verify_python.__code__, namespace)
    helper = native_module.bind_token_verifier(fallback)
    del namespace[name]
    with pytest.raises(NameError, match=name):
        helper(_KEY, token.mint(_KEY, _CLAIMS), 8192)


def test_native_binding_and_fallback_reference_cycles_are_collectible(native_module):
    namespace = dict(token.__dict__)
    fallback = FunctionType(token._verify_python.__code__, namespace)
    helper = native_module.bind_token_verifier(fallback)
    namespace["cycle"] = (fallback, helper)
    fallback_reference = weakref.ref(fallback)
    helper_reference = weakref.ref(helper)
    del fallback, helper, namespace
    gc.collect()
    assert fallback_reference() is None
    assert helper_reference() is None


def test_native_does_not_retain_request_keys_or_callback_results(native_module):
    namespace = dict(token.__dict__)
    fallback = FunctionType(token._verify_python.__code__, namespace)
    helper = native_module.bind_token_verifier(fallback)
    bearer = token.mint(_KEY, _CLAIMS)
    references = []

    class Key:
        pass

    class Match:
        def __bool__(self):
            return True

    def mac(_key, _payload):
        return b"x" * 32

    def compare(_supplied, _expected):
        match = Match()
        references.append(weakref.ref(match))
        return match

    namespace["mac_raw"] = mac
    namespace["equal"] = compare
    key = Key()
    key_reference = weakref.ref(key)
    assert helper(key, bearer, 8192) == _CLAIMS
    del key
    gc.collect()
    assert key_reference() is None
    assert all(reference() is None for reference in references)
    tracked = (fallback, namespace, mac, compare, token.b64, token.b64.decode)
    before = [sys.getrefcount(value) for value in tracked]
    for _ in range(100):
        assert helper(_KEY, bearer, 8192) == _CLAIMS
        assert helper(_KEY, "malformed", 8192) is None
    assert [sys.getrefcount(value) for value in tracked] == before


@pytest.mark.parametrize("raise_error", [False, True])
def test_native_releases_callback_operands_on_refusal_and_exception(
    native_module, raise_error
):
    namespace = dict(token.__dict__)
    fallback = FunctionType(token._verify_python.__code__, namespace)
    helper = native_module.bind_token_verifier(fallback)
    bearer = token.mint(_KEY, _CLAIMS)
    references = []

    class Key:
        pass

    class Decoded:
        pass

    def decode(_value, *, url):
        decoded = Decoded()
        references.append(weakref.ref(decoded))
        return decoded

    def compare(_supplied, _expected):
        if raise_error:
            message = "comparison refused"
            raise RuntimeError(message)
        return False

    namespace["b64"] = SimpleNamespace(decode=decode)
    namespace["mac_raw"] = lambda _key, _payload: b"x" * 32
    namespace["equal"] = compare
    key = Key()
    key_reference = weakref.ref(key)
    if raise_error:
        with pytest.raises(RuntimeError, match="comparison refused") as caught:
            helper(key, bearer, 8192)
        # Python callback tracebacks own their frames and operands by design.
        caught.value.__traceback__ = None
        del caught
    else:
        assert helper(key, bearer, 8192) is None
    del key
    gc.collect()
    assert key_reference() is None
    assert all(reference() is None for reference in references)


def test_large_limits_and_string_subclass_partition_hooks_agree(verify):
    bearer = token.mint(_KEY, b"x" * 65536)
    assert verify(_KEY, bearer) is None
    assert verify(_KEY, bearer, max_token_chars=1 << 10000) == b"x" * 65536
    with pytest.raises(ValueError, match="positive integer or None"):
        verify(_KEY, "", max_token_chars=-(1 << 10000))

    class Text(str):
        def __len__(self):
            return 1

        def __getitem__(self, _index):
            pytest.fail("a string subclass keeps its partition hook")

        def partition(self, separator):
            return token.mint(_KEY, _CLAIMS).partition(separator)

    # The bounded path uses stored text length before consulting any hook.
    assert verify(_KEY, Text("x")) is None
    assert verify(_KEY, Text("x"), max_token_chars=None) == _CLAIMS


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="requires interval timers")
def test_native_unbounded_alphabet_scan_remains_interruptible(native_module):
    script = """
import signal
import sys
from types import SimpleNamespace
from unstd.crypto import token

class Interrupted(Exception):
    pass

verify = token.prepare_verifier(max_token_chars=None)
key = bytes(32)
candidate = token.mint(key, b'signal calibration')
active_references = []
original_b64 = token.b64

def count_active_references(value, *, url):
    active_references.append(sys.getrefcount(candidate))
    return original_b64.decode(value, url=url)

token.b64 = SimpleNamespace(decode=count_active_references)
assert verify(key, candidate) == b'signal calibration'
token.b64 = original_b64
candidate = 'A' * (128 * 1024 * 1024 - 2) + '!A.' + 'A' * 43
observed = []

def interrupt(signum, frame):
    observed.append(sys.getrefcount(candidate))
    raise Interrupted('interrupt canonicality scan')

signal.signal(signal.SIGALRM, interrupt)
signal.setitimer(signal.ITIMER_REAL, 0.005)
try:
    verify(key, candidate)
except Interrupted:
    print('INTERRUPTED_UNBOUNDED_SCAN')
else:
    raise AssertionError('native scan did not observe the pending signal')
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
assert observed
# While the C call is active its argument stack still owns the input. A signal
# delivered only after C returns has one fewer reference. Calibrate from a real
# callback instead of relying on a CPython-version-specific reference count.
assert observed[0] >= active_references[0], (observed, active_references)
"""
    result = subprocess.run(  # noqa: S603  # bounded isolated signal fixture
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "INTERRUPTED_UNBOUNDED_SCAN\n"
