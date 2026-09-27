"""WAV read/write with numpy-native samples — a stdlib-``wave`` replacement.

Where stdlib ``wave`` hands back packed PCM byte strings, ``read``/``write``
speak in sample arrays: float32 in ``[-1, 1]`` (or int16) on the way in, the same
on the way out. Mono is 1-D ``(frames,)``; multichannel is 2-D ``(frames,
channels)`` — the shape libsndfile / ``soundfile`` use.

``wrap_pcm`` is the third operation, and the only one that stays in bytes: a
header around PCM that arrived already packed (off a socket, out of a codec), for
callers whose samples were never samples and would only be decoded to be
re-encoded.

Backend & fallback:

- ``soundfile`` (Bastian Bechtold's bindings over Erik de Castro Lopo's
  **libsndfile**) + ``numpy``, via the ``audio`` extra — the fast path. Samples
  are ``numpy.ndarray``.
- else stdlib ``wave`` + ``numpy`` — manual ``frombuffer`` PCM decode / ``tobytes``
  encode. Samples are ``numpy.ndarray``; supports 8/16/32-bit read, PCM_16/PCM_32
  write.
- else stdlib ``wave`` + ``array`` — the pure-stdlib floor. Samples are Python
  ``list`` (mono) or ``list[list]`` (multichannel). **Reduced ergonomics: 16-bit
  PCM only** (both directions); wider widths raise a clear error pointing at the
  ``audio`` extra.

Sample scaling is fixed-point and identical across every path (so a file written
by one reads back consistently on another): a *w*-bit PCM sample maps to float by
dividing by ``2**(w-1)``, and a float maps back by multiplying by ``2**(w-1)`` and
clamping into the signed range. Round-tripping ``float32 → PCM16 → float32`` is
therefore lossless to within one quantization step (``1/32768``).

Prior art: ``soundfile`` (github.com/bastibe/python-soundfile) over libsndfile
(libsndfile.github.io); the stdlib ``wave`` module this surface replaces.
"""

from __future__ import annotations

import array
from collections.abc import Callable, Iterator, Sequence
import contextlib
import struct
import sys
from typing import IO, TYPE_CHECKING, Protocol, cast
import wave


if TYPE_CHECKING:
    import numpy as _np
    from numpy.typing import NDArray

    class _SoundFileHandle(Protocol):
        """The slice of a live ``soundfile.SoundFile`` handle this file touches — the streaming counterpart to the module-level ``read``/``write`` functions below."""

        def write(self, data: NDArray[_np.generic]) -> None: ...
        def close(self) -> None: ...
        def __enter__(self) -> _SoundFileHandle: ...
        def __exit__(self, *exc: object) -> None: ...

    class _Soundfile(Protocol):
        """The slice of the ``soundfile`` module surface this file touches, typed to the *runtime* behavior — file objects are accepted for read/write, which the published stubs (path-only ``file`` params) still lag behind."""

        def read(
            self,
            file: PathOrFile,
            *,
            dtype: str = ...,
            always_2d: bool = ...,
        ) -> tuple[NDArray[_np.generic], int]: ...
        def write(
            self,
            file: PathOrFile,
            data: NDArray[_np.generic],
            samplerate: int,
            *,
            subtype: str | None = ...,
        ) -> None: ...
        def blocks(
            self,
            file: PathOrFile,
            *,
            blocksize: int,
            dtype: str = ...,
            always_2d: bool = ...,
        ) -> Iterator[NDArray[_np.generic]]: ...
        def SoundFile(  # noqa: N802 — mirrors soundfile's own PascalCase class
            self,
            file: PathOrFile,
            mode: str = ...,
            samplerate: int | None = ...,
            channels: int | None = ...,
            subtype: str | None = ...,
        ) -> _SoundFileHandle: ...

    _sf: _Soundfile
    _HAVE_SOUNDFILE = True
    _HAVE_NUMPY = True
else:
    try:  # the `audio` extra — libsndfile bindings; pulls numpy transitively
        import soundfile as _sf

        _HAVE_SOUNDFILE = True
    except ImportError:  # base install — stdlib `wave` fallback
        _sf = None
        _HAVE_SOUNDFILE = False

    try:
        import numpy as _np

        _HAVE_NUMPY = True
    except ImportError:  # no numpy — stdlib `array` floor (16-bit PCM only)
        _np = None
        _HAVE_NUMPY = False


__all__ = ["Writer", "blocks", "read", "wrap_pcm", "write"]

# A filesystem path or a binary file object (rb / wb).
type PathOrFile = str | IO[bytes]
# numpy.ndarray when numpy is present, else list / list[list] (1-D mono, 2-D multichannel).
type Samples = NDArray[_np.generic] | Sequence[float] | Sequence[Sequence[float]]

_DTYPES = ("float32", "float64", "int16")
# stdlib-fallback little-endian PCM dtype codes by sample width (bytes).
_PCM_CODE = {1: "<u1", 2: "<i2", 4: "<i4"}
# write subtype → (sample width, LE dtype code, full-scale magnitude 2**(w-1)).
_SUBTYPE = {"PCM_16": (2, "<i2", 1 << 15), "PCM_32": (4, "<i4", 1 << 31)}


def read(path_or_file: PathOrFile, *, dtype: str = "float32") -> tuple[Samples, int]:
    """Read a WAV file into ``(samples, samplerate)``.

    ``dtype`` is ``"float32"`` (default) or ``"float64"`` for normalized samples in
    ``[-1, 1]``, or ``"int16"`` for raw signed 16-bit samples. Mono returns a 1-D
    container, multichannel a 2-D ``(frames, channels)`` one.
    """
    if dtype not in _DTYPES:
        msg = f"dtype must be one of {_DTYPES}, got {dtype!r}"
        raise ValueError(msg)
    if _HAVE_SOUNDFILE:
        samples, samplerate = _sf.read(path_or_file, dtype=dtype, always_2d=False)
        return samples, int(samplerate)
    with contextlib.closing(wave.open(path_or_file, "rb")) as w:
        nchannels, sampwidth = w.getnchannels(), w.getsampwidth()
        samplerate, nframes = w.getframerate(), w.getnframes()
        raw = w.readframes(nframes)
    reader = _read_numpy if _HAVE_NUMPY else _read_array
    return reader(raw, nchannels, sampwidth, dtype), samplerate


def blocks(
    path_or_file: PathOrFile, blocksize: int, *, dtype: str = "float32"
) -> Iterator[Samples]:
    """Stream a WAV file *blocksize* frames at a time, instead of loading it whole.

    The chunked counterpart to :func:`read`, for the same reason
    :class:`Writer` exists on the write side: a live consumer (playback,
    streaming transcription) wants frames as they become available rather than
    after the whole file has been decoded into one array. Same ``dtype``
    contract as :func:`read`; each yielded chunk has the same mono/
    multichannel shape :func:`read` returns, just ``blocksize`` frames long
    (the final chunk may be shorter). Fast path: ``soundfile.blocks``. Fallback:
    a ``wave.readframes(blocksize)`` loop decoded through the same helpers
    :func:`read` uses on its own fallback path — identical numbers, one
    chunk at a time instead of the whole file at once.
    """
    if dtype not in _DTYPES:
        msg = f"dtype must be one of {_DTYPES}, got {dtype!r}"
        raise ValueError(msg)
    if _HAVE_SOUNDFILE:
        yield from _sf.blocks(
            path_or_file, blocksize=blocksize, dtype=dtype, always_2d=False
        )
        return
    reader = _read_numpy if _HAVE_NUMPY else _read_array
    with contextlib.closing(wave.open(path_or_file, "rb")) as w:
        nchannels, sampwidth = w.getnchannels(), w.getsampwidth()
        while raw := w.readframes(blocksize):
            yield reader(raw, nchannels, sampwidth, dtype)


def wrap_pcm(
    raw: bytes,
    samplerate: int,
    *,
    channels: int = 1,
    width: int = 2,
) -> bytes:
    """Wrap already-interleaved, little-endian PCM bytes in a RIFF/WAVE container.

    The complement of :func:`write`, for the case where the samples never
    became samples. A caller holding PCM straight off a wire — a codec's output,
    a socket frame, a transcription upload — wants a *file* around bytes it
    already has, and routing that through ``write`` would decode to an array
    and re-quantize back, spending two conversions and a quantization step to
    reproduce its own input. Nothing is scaled, clamped or reinterpreted here:
    ``raw`` lands in the data chunk verbatim.

    ``width`` is bytes per sample (2 = PCM16), ``channels`` is the interleave
    factor, and both belong to the bytes rather than to a preference — get them
    wrong and the file plays at the wrong speed or pitch, which is the one
    failure a header cannot detect.
    """
    # The canonical 44-byte PCM header, packed in one call — byte-identical to what
    # `wave` writes (pinned in the suite), at a sixth of the cost of a writer
    # object built only to emit it. Same refusals `wave` makes, same exception.
    channels, rate = max(1, channels), int(samplerate)
    if not 1 <= width <= 4:
        msg = "sample width not specified"
        raise wave.Error(msg)
    if rate <= 0:
        msg = "sampling rate not specified"
        raise wave.Error(msg)
    n = len(raw)
    block = channels * width
    return (
        _RIFF_PCM.pack(
            b"RIFF",
            36 + n,
            b"WAVE",
            b"fmt ",
            16,
            1,
            channels,
            rate,
            rate * block,
            block,
            8 * width,
            b"data",
            n,
        )
        + raw
    )


def write(
    path_or_file: PathOrFile,
    samples: Samples,
    samplerate: int,
    *,
    subtype: str = "PCM_16",
) -> None:
    """Write ``samples`` to a WAV file at ``samplerate`` Hz.

    ``samples`` may be float (clamped to ``[-1, 1]`` then quantized) or integer
    (written as-is). 1-D is mono; 2-D ``(frames, channels)`` is multichannel.
    ``subtype`` follows ``soundfile`` naming (``"PCM_16"`` default, ``"PCM_32"``,
    ``"FLOAT"``, …); the stdlib fallback supports ``PCM_16``/``PCM_32`` (numpy) or
    ``PCM_16`` only (array floor).
    """
    if _HAVE_SOUNDFILE:
        _sf.write(path_or_file, _sf_samples(samples), int(samplerate), subtype=subtype)
        return
    writer = _write_numpy if _HAVE_NUMPY else _write_array
    writer(path_or_file, samples, int(samplerate), subtype)


class Writer:
    """Incremental WAV writer — append frames as they arrive.

    Nothing buffers a whole utterance before the first byte reaches disk. The
    streaming counterpart to :func:`write`: live TTS synthesis produces
    audio in chunks (the voice paths ``unstd.audio``'s own module docstring
    names as the target), and routing every chunk through a fresh
    :func:`write` call would rewrite the file from scratch each time. Use
    as a context manager — ``channels``/``subtype`` are fixed for the file's
    lifetime (unlike :func:`write`, which infers the channel count from a
    complete array, a stream has no "complete array" to infer from), every
    :meth:`write` call appends one chunk, and ``close()`` finalizes the header.

    Fast path (``audio`` extra): wraps a ``soundfile.SoundFile`` opened in
    write mode — its writer already streams. Fallback: stdlib ``wave`` +
    ``writeframesraw``, which defers the RIFF/``data``-chunk sizes to
    ``close()`` — this **requires `path_or_file` be seekable** (a real file
    path, or a file object opened on one); a genuinely non-seekable sink (a raw
    socket) needs the ``audio`` extra.
    """

    def __init__(
        self,
        path_or_file: PathOrFile,
        samplerate: int,
        *,
        channels: int = 1,
        subtype: str = "PCM_16",
    ) -> None:
        """Open *path_or_file* for streaming writes at *samplerate*.

        ``channels`` and ``subtype`` are fixed for the file's lifetime; an
        unsupported ``subtype`` on either fallback raises ``ValueError`` here
        rather than on the first :meth:`write`.
        """
        self._sf_handle: _SoundFileHandle | None = None
        self._wave: wave.Wave_write | None = None
        self._spec: tuple[int, str, int] | None = None
        if _HAVE_SOUNDFILE:
            self._sf_handle = _sf.SoundFile(
                path_or_file,
                mode="w",
                samplerate=int(samplerate),
                channels=channels,
                subtype=subtype,
            )
            return
        self._spec = _SUBTYPE.get(subtype) if _HAVE_NUMPY else None
        if (_HAVE_NUMPY and self._spec is None) or (
            not _HAVE_NUMPY and subtype != "PCM_16"
        ):
            raise ValueError(_bad_subtype(subtype))
        # SIM115: the handle is owned by this object, not by a block — it must
        # outlive __init__ so successive `write` calls can append to it. The
        # lifetime is closed by `close()` / __exit__, which is what makes this a
        # streaming writer rather than a one-shot `write`.
        self._wave = wave.open(path_or_file, "wb")  # noqa: SIM115
        self._wave.setnchannels(channels)
        self._wave.setsampwidth(self._spec[0] if self._spec else 2)
        self._wave.setframerate(int(samplerate))

    def write(self, samples: Samples) -> None:
        """Append one chunk of samples — same shape/dtype rules as :func:`write`."""
        if self._sf_handle is not None:
            self._sf_handle.write(_sf_samples(samples))
            return
        # __init__ binds exactly one handle or raises, so this is the `wave`
        # branch by elimination — hoisted into a local so the invariant is stated
        # once and carried by the type, rather than re-assumed at each write.
        wav = self._wave
        if wav is None:
            msg = "Writer has no open handle — was close() already called?"
            raise ValueError(msg)
        if self._spec is not None:  # numpy fallback
            _, code, scale = self._spec
            arr = _np.asarray(samples)
            ints = (
                _np.round(_np.clip(arr, -1.0, 1.0) * scale)
                if _np.issubdtype(arr.dtype, _np.floating)
                else arr
            )
            ints = _np.ascontiguousarray(_np.clip(ints, -scale, scale - 1).astype(code))
            wav.writeframesraw(ints.tobytes())
        else:  # pure-stdlib array floor — 16-bit PCM only
            flat, _nchannels = _flatten(samples)
            pcm = array.array("h", (_to_int16(v) for v in flat))
            if sys.byteorder == "big":
                pcm.byteswap()
            wav.writeframesraw(pcm.tobytes())

    def close(self) -> None:
        """Finalize the header (patching RIFF/``data`` sizes if needed) and close the file."""
        if self._sf_handle is not None:
            self._sf_handle.close()
        elif self._wave is not None:
            self._wave.close()

    def __enter__(self) -> Writer:
        """Enter the writer's context, returning self."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Finalize and close the file on context exit, success or exception."""
        self.close()


def _sf_samples(samples: Samples) -> NDArray[_np.generic]:
    """Coerce integer samples to a libsndfile-accepted dtype.

    A plain Python ``list`` of ints (or a ``numpy`` int64 array) is ambiguous —
    ``numpy`` widens it to int64, which libsndfile rejects. Downcast any integer
    input that isn't already int16/int32 to int16 (the documented integer sample
    type), matching what the stdlib fallback does. Float input passes through.
    """
    arr = _np.asarray(samples)
    if _np.issubdtype(arr.dtype, _np.integer) and arr.dtype not in (
        _np.int16,
        _np.int32,
    ):
        return arr.astype(_np.int16)
    return arr


# ── stdlib + numpy path ────────────────────────────────────────────────────────


def _read_numpy(raw: bytes, nchannels: int, sampwidth: int, dtype: str) -> Samples:
    code = _PCM_CODE.get(sampwidth)
    if code is None:
        raise ValueError(_bad_width(sampwidth, "8/16/32"))
    arr = _np.frombuffer(raw, dtype=code)
    if sampwidth == 1:  # 8-bit WAV is unsigned, centered on 128
        flt = (arr.astype(_np.float64) - 128.0) / 128.0
    else:
        flt = arr.astype(_np.float64) / float(1 << (8 * sampwidth - 1))
    if nchannels > 1:
        flt = flt.reshape(-1, nchannels)
    if dtype == "int16":
        return _f2i16_numpy(flt)
    return flt.astype(_np.float32 if dtype == "float32" else _np.float64)


def _f2i16_numpy(flt: NDArray[_np.floating]) -> NDArray[_np.int16]:
    scaled = _np.round(_np.clip(flt, -1.0, 1.0) * 32768.0)
    # `"<i2"` and not `_np.int16`: WAV samples are little-endian by spec, and the
    # native dtype would silently emit big-endian frames on a BE host. numpy types
    # `astype` with a dtype *string* as `Any`, hence the cast.
    return cast("NDArray[_np.int16]", _np.clip(scaled, -32768, 32767).astype("<i2"))


def _write_numpy(
    fp: PathOrFile, samples: Samples, samplerate: int, subtype: str
) -> None:
    spec = _SUBTYPE.get(subtype)
    if spec is None:
        raise ValueError(_bad_subtype(subtype))
    width, code, scale = spec
    arr = _np.asarray(samples)
    nchannels = 1 if arr.ndim == 1 else arr.shape[1]
    if _np.issubdtype(arr.dtype, _np.floating):
        ints = _np.round(_np.clip(arr, -1.0, 1.0) * scale)
    else:
        ints = arr
    ints = _np.ascontiguousarray(_np.clip(ints, -scale, scale - 1).astype(code))
    _write_frames(fp, ints.tobytes(), nchannels, width, samplerate)


# ── pure-stdlib `array` floor (16-bit PCM only) ────────────────────────────────


def _read_array(raw: bytes, nchannels: int, sampwidth: int, dtype: str) -> Samples:
    if sampwidth != 2:
        raise ValueError(_bad_width(sampwidth, "16"))
    a = array.array("h")  # signed 16-bit
    a.frombytes(raw)
    if sys.byteorder == "big":  # WAV PCM is little-endian
        a.byteswap()
    conv: Callable[[int], float] = (
        (lambda v: v) if dtype == "int16" else (lambda v: v / 32768.0)
    )
    if nchannels == 1:
        return [conv(v) for v in a]
    return [
        [conv(a[i + c]) for c in range(nchannels)] for i in range(0, len(a), nchannels)
    ]


def _write_array(
    fp: PathOrFile, samples: Samples, samplerate: int, subtype: str
) -> None:
    if subtype != "PCM_16":
        raise ValueError(_bad_subtype(subtype))
    flat, nchannels = _flatten(samples)
    ints = array.array("h", (_to_int16(v) for v in flat))
    if sys.byteorder == "big":
        ints.byteswap()
    _write_frames(fp, ints.tobytes(), nchannels, 2, samplerate)


def _flatten(samples: Samples) -> tuple[list[float], int]:
    seq: list[object] = list(samples)
    if seq and isinstance(seq[0], list | tuple):
        nchannels = len(seq[0])
        return [v for frame in seq for v in _frame(frame)], nchannels
    return [_scalar(v) for v in seq], 1


def _frame(frame: object) -> Sequence[float]:
    """Narrow one multichannel frame to a sequence — mixed mono/frame input is a bug."""
    if isinstance(frame, list | tuple):
        return frame
    msg = f"expected a per-frame sequence of samples, got {type(frame).__name__}"
    raise TypeError(msg)


def _scalar(v: object) -> float:
    """Narrow one mono sample to a number — anything else is a bug."""
    if isinstance(v, int | float):
        return v
    msg = f"expected a numeric sample, got {type(v).__name__}"
    raise TypeError(msg)


def _to_int16(v: float) -> int:
    scaled = round(max(-1.0, min(1.0, v)) * 32768.0) if isinstance(v, float) else int(v)
    return max(-32768, min(32767, scaled))


# ── shared ─────────────────────────────────────────────────────────────────────


def _write_frames(
    fp: PathOrFile, raw: bytes, nchannels: int, width: int, samplerate: int
) -> None:
    with contextlib.closing(wave.open(fp, "wb")) as w:
        w.setnchannels(nchannels)
        w.setsampwidth(width)
        w.setframerate(samplerate)
        w.writeframes(raw)


# RIFF/WAVE container + `fmt ` chunk (PCM, 16 bytes) + `data` chunk header.
_RIFF_PCM = struct.Struct("<4sI4s4sIHHIIHH4sI")

_INSTALL_HINT = "install the 'audio' extra (soundfile): pip install 'unstd[audio]'"


def _bad_width(sampwidth: int, supported: str) -> str:
    return (
        f"unstd.audio stdlib fallback supports {supported}-bit PCM, got "
        f"{sampwidth * 8}-bit — {_INSTALL_HINT}"
    )


def _bad_subtype(subtype: str) -> str:
    return f"unstd.audio stdlib fallback cannot write subtype {subtype!r} — {_INSTALL_HINT}"
