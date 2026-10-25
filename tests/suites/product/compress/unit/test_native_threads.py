"""Native streams release the GIL while serializing shared decoder state."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest


@pytest.fixture
def native_module():
    return pytest.importorskip("unstd.compress._gzip_native")


def _isolated(source: str) -> None:
    result = subprocess.run(  # noqa: S603 — fixed native safety probe and this interpreter
        [sys.executable, "-c", textwrap.dedent(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("direct", [False, True])
def test_shared_inflater_serializes_bounded_readers(native_module, direct):
    _isolated(
        f"DIRECT = {direct!r}\n"
        + """\
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import threading
import zlib
from unstd.compress._gzip_native import Inflater

size = 128 * 1024
payload = b''.join(bytes([index]) * size for index in range(64))
packed = zlib.compress(payload, wbits=-15)
decoder = Inflater()
assert decoder.decompress(packed, 0) == b''
barrier = threading.Barrier(4)

def consume():
    result = []
    target = bytearray(size)
    barrier.wait()
    while True:
        try:
            chunk = decoder.decompress_into(b'', target) if DIRECT else decoder.decompress(b'', size)
        except EOFError:
            return result
        if not chunk:
            return result
        result.append(bytes(chunk))

with ThreadPoolExecutor(max_workers=4) as pool:
    pieces = [chunk for chunks in pool.map(lambda _: consume(), range(4)) for chunk in chunks]
assert Counter(pieces) == Counter(payload[index:index+size] for index in range(0, len(payload), size))
assert decoder.eof and not decoder.needs_input
assert decoder.unused_data == b''
"""
    )


@pytest.mark.parametrize("direct", [False, True])
def test_large_inflate_allows_other_python_thread_to_progress(native_module, direct):
    _isolated(
        f"DIRECT = {direct!r}\n"
        + """\
import sys
import threading
import zlib
from unstd.compress._gzip_native import Inflater

from random import Random
# Huffman-heavy input keeps inflation active across an OS time slice even
# with one allowed CPU; a short repeated-match decode can finish before the
# waiting Python thread is scheduled despite correctly releasing the GIL.
payload = Random(173).randbytes(16 * 1024 * 1024).translate(bytes(i & 31 for i in range(256)))
packed = zlib.compress(payload, wbits=-15)
decoder = Inflater()
target = bytearray(len(payload)) if DIRECT else None
ready, waiting = threading.Event(), threading.Event()
finished = False
observed = []

def observe():
    waiting.set()
    ready.wait()
    observed.append(not finished)

thread = threading.Thread(target=observe)
thread.start()
assert waiting.wait(5)
previous = sys.getswitchinterval()
sys.setswitchinterval(30)
try:
    ready.set()
    result = decoder.decompress_into(packed, target) if DIRECT else decoder.decompress(packed)
    finished = True
finally:
    sys.setswitchinterval(previous)
thread.join(5)
assert not thread.is_alive()
assert observed == [True], 'Python thread could not run during native inflation'
assert bytes(result) == payload
assert decoder.eof
"""
    )


@pytest.mark.parametrize("direct", [False, True])
def test_reinitialization_waits_for_running_inflate(native_module, direct):
    _isolated(
        f"DIRECT = {direct!r}\n"
        + """\
import sys
import threading
import zlib
from unstd.compress._gzip_native import Inflater

from random import Random
# Huffman-heavy input keeps inflation active across an OS time slice even
# with one allowed CPU; a short repeated-match decode can finish before the
# waiting Python thread is scheduled despite correctly releasing the GIL.
payload = Random(173).randbytes(16 * 1024 * 1024).translate(bytes(i & 31 for i in range(256)))
packed = zlib.compress(payload, wbits=-15)
decoder = Inflater()
target = bytearray(len(payload)) if DIRECT else None
ready, waiting = threading.Event(), threading.Event()
finished = False
started = []
errors = []

def reset():
    waiting.set()
    ready.wait()
    started.append(not finished)
    try:
        decoder.__init__()
    except BaseException as error:
        errors.append(error)

thread = threading.Thread(target=reset)
thread.start()
assert waiting.wait(5)
previous = sys.getswitchinterval()
sys.setswitchinterval(30)
try:
    ready.set()
    result = decoder.decompress_into(packed, target) if DIRECT else decoder.decompress(packed)
    finished = True
finally:
    sys.setswitchinterval(previous)
thread.join(5)
assert not thread.is_alive(), 'reinitialization waited while holding the GIL'
assert not errors
assert started == [True], 'reset did not overlap native inflation'
assert bytes(result) == payload
assert not decoder.eof and decoder.needs_input
assert decoder.decompress(zlib.compress(b'after reset', wbits=-15)) == b'after reset'
"""
    )


def test_error_translation_can_reenter_decoder_after_native_failure(native_module):
    _isolated(
        """\
import sys
import types
import zlib
from unstd.compress._gzip_native import Inflater

decoder = Inflater()
original = zlib
callbacks = []

class Proxy(types.ModuleType):
    def __getattr__(self, name):
        if name == 'error':
            callbacks.append(name)
            decoder.__init__()
        return getattr(original, name)

sys.modules['zlib'] = Proxy('zlib')
try:
    try:
        decoder.decompress(b'\x07')
    except original.error as error:
        assert str(error) == 'Error -3 while decompressing data: invalid block type'
    else:
        raise AssertionError('malformed DEFLATE was accepted')
finally:
    sys.modules['zlib'] = original
assert callbacks == ['error']
assert decoder.decompress(zlib.compress(b'usable', wbits=-15)) == b'usable'
"""
    )
