"""WAV read/write with numpy-native samples — a stdlib-``wave`` replacement.

Where stdlib ``wave`` hands back packed PCM byte strings, ``read``/``write``
speak in sample arrays: float32 in ``[-1, 1]`` (or int16) on the way in, the same
on the way out. Mono is 1-D ``(frames,)``; multichannel is 2-D ``(frames,
channels)`` — the shape libsndfile / ``soundfile`` use.

``wrap_pcm`` stays in bytes: a header around PCM that arrived already packed
(off a socket, out of a codec). ``decode_pcm`` converts raw PCM directly to
independent samples for live voice consumers, without a WAV round trip.

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

from collections.abc import Buffer, Iterator, Sequence
import contextlib
from operator import index
import struct
from typing import IO, TYPE_CHECKING, Protocol, Self, cast
import wave

from unstd.audio._internal.pcm import _bad_subtype as _bad_subtype
from unstd.audio._internal.pcm import _bad_width as _bad_width
from unstd.audio._internal.pcm import _check_channels as _check_channels
from unstd.audio._internal.pcm import _check_dtype as _check_dtype
from unstd.audio._internal.pcm import _check_pcm_frames as _check_pcm_frames
from unstd.audio._internal.pcm import _pcm16 as _pcm16
from unstd.audio._internal.pcm import _pcm_buffer as _pcm_buffer
from unstd.audio._internal.pcm import _python_integers as _python_integers
from unstd.audio._internal.pcm import _read_array as _read_array


if TYPE_CHECKING:
    import numpy as _np
    from numpy.typing import NDArray

    class _SoundFileHandle(Protocol):
        """The slice of a live ``soundfile.SoundFile`` handle this file touches — the streaming counterpart to the module-level ``read``/``write`` functions below."""

        def write(self, data: NDArray[_np.generic]) -> None: ...
        def close(self) -> None: ...
        def __enter__(self) -> Self: ...
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
            format: str | None = ...,
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
            format: str | None = ...,
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


__all__ = ["Writer", "blocks", "decode_pcm", "read", "wrap_pcm", "write"]

# A filesystem path or a binary file object (rb / wb).
type PathOrFile = str | IO[bytes]
# numpy.ndarray when numpy is present, else list / list[list] (1-D mono, 2-D multichannel).
type Samples = NDArray[_np.generic] | Sequence[float] | Sequence[Sequence[float]]

# stdlib-fallback little-endian PCM dtype codes by sample width (bytes).
_PCM_CODE = {1: "<u1", 2: "<i2", 4: "<i4"}
# write subtype → (sample width, LE dtype code, full-scale magnitude 2**(w-1)).
_SUBTYPE = {"PCM_16": (2, "<i2", 1 << 15), "PCM_32": (4, "<i4", 1 << 31)}


def read(path_or_file: PathOrFile, *, dtype: str = "float32") -> tuple[Samples, int]:
    """Read a WAV file into ``(samples, samplerate)``.

    ``dtype`` is ``"float32"`` (default) or ``"float64"`` for normalized samples in
    ``[-1, 1]``, or ``"int16"`` for raw signed 16-bit samples. Mono returns a 1-D
    container, multichannel a 2-D ``(frames, channels)`` one. Fallback decoders
    reject incomplete PCM sample/channel frames with ``ValueError``.
    """
    _check_dtype(dtype)
    if _HAVE_SOUNDFILE:
        samples, samplerate = _sf.read(path_or_file, dtype=dtype, always_2d=False)
        return samples, int(samplerate)
    with contextlib.closing(wave.open(path_or_file, "rb")) as w:
        nchannels, sampwidth = w.getnchannels(), w.getsampwidth()
        samplerate, nframes = w.getframerate(), w.getnframes()
        # getnframes() floors the data chunk's byte length. Read one extra frame
        # so partial sample/channel bytes reach the shared alignment check too;
        # wave still bounds the read to this data chunk, excluding later chunks.
        raw = w.readframes(nframes + 1)
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

    ``blocksize`` must be positive; nonpositive sizes raise ``ValueError``
    before the input is opened or read.
    """
    _check_dtype(dtype)
    if blocksize <= 0:
        msg = "blocksize must be positive"
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
    raw: Buffer,
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
    ``raw`` lands in the data chunk verbatim. Accepts bytes, bytearray and other
    C-contiguous buffers, including typed memoryviews; sizes count bytes, not
    elements. The returned bytes own their storage, so a mutable source can be
    reused after the call. Pass an accumulated bytearray directly to avoid an
    intermediate ``bytes(raw)`` copy. Noncontiguous buffers raise ``BufferError``.

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
    if isinstance(raw, bytes):
        buffer: bytes | bytearray | memoryview = raw
        n = len(raw)
    else:
        buffer = _pcm_buffer(raw)
        n = buffer.nbytes if isinstance(buffer, memoryview) else len(buffer)
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
        + buffer
    )


def decode_pcm(
    raw: Buffer, *, channels: int = 1, width: int = 2, dtype: str = "float32"
) -> Samples:
    """Decode interleaved little-endian PCM without constructing a WAV file.

    ``width`` is bytes per sample: 1 is unsigned PCM8, 2/4 are signed PCM16/32.
    ``dtype`` is ``"float32"`` (default), ``"float64"`` or ``"int16"``, with the
    same scaling and mono/multichannel shape as :func:`read`. The sample rate
    belongs to the caller and is not needed for conversion.

    NumPy returns independent writable arrays, with one output allocation for
    floating samples. Without NumPy, only PCM16 is supported and the result is
    a Python list. No soundfile dependency or codec is used. ``channels`` must
    be a positive integer and the input must contain complete frames.
    C-contiguous buffers, including bytearrays and typed memoryviews, are
    accepted; noncontiguous buffers raise ``BufferError``. The source is never
    modified or retained by the result.
    """
    _check_dtype(dtype)
    channels, width = index(channels), index(width)
    if channels <= 0:
        msg = "channels must be positive"
        raise ValueError(msg)
    reader = _read_numpy if _HAVE_NUMPY else _read_array
    return reader(_pcm_buffer(raw), channels, width, dtype)


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
        _sf.write(
            path_or_file,
            _sf_samples(samples, subtype),
            int(samplerate),
            subtype=subtype,
            format="WAV",
        )
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
    path, or a file object opened on one). libsndfile also seeks to finalize
    WAV output, so the ``audio`` extra does not make a raw socket a supported
    sink either.
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
        self._subtype = subtype
        if _HAVE_SOUNDFILE:
            self._sf_handle = _sf.SoundFile(
                path_or_file,
                mode="w",
                samplerate=int(samplerate),
                channels=channels,
                subtype=subtype,
                format="WAV",
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
            self._sf_handle.write(_sf_samples(samples, self._subtype))
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
            arr = _sample_array(samples)
            _check_channels(
                wav, 1 if arr.ndim == 1 else arr.shape[1] if arr.ndim == 2 else 0
            )
            wav.writeframesraw(_quantize(arr, code, scale))
        else:  # pure-stdlib array floor — 16-bit PCM only
            raw, nchannels = _pcm16(samples)
            _check_channels(wav, nchannels)
            wav.writeframesraw(raw)

    def close(self) -> None:
        """Finalize the header (patching RIFF/``data`` sizes if needed) and close the file."""
        if self._sf_handle is not None:
            self._sf_handle.close()
        elif self._wave is not None:
            wav, self._wave = self._wave, None
            wav.close()

    def __enter__(self) -> Self:
        """Enter the writer's context, returning self."""
        return self

    def __exit__(self, *exc: object) -> None:
        """Finalize and close the file on context exit, success or exception."""
        self.close()


def _sample_array(samples: Samples) -> NDArray[_np.generic]:
    """Keep mixed signed/unsigned Python integers out of NumPy's float promotion."""
    arr = _np.asarray(samples)
    if _np.issubdtype(arr.dtype, _np.floating) and _python_integers(samples):
        # [-1, 2**63] has no common native integer dtype. Preserve counts until
        # clipping rather than interpreting -1 as normalized negative full scale.
        arr = _np.asarray(samples, dtype=object)
    return arr


def _sf_samples(samples: Samples, subtype: str) -> NDArray[_np.generic]:
    """Prepare integer sample counts without libsndfile's source-width scaling.

    libsndfile aligns integer inputs to the destination's full-scale range: an
    int16 value written as PCM32 is shifted left by 16 bits, and an int32 value
    written as PCM16 loses its low 16 bits. Our PCM16/PCM32 contract instead uses
    raw integer sample counts. Clip before converting to the destination width,
    matching the fallback quantizer and avoiding overflow from a narrowing cast.
    Object arrays of Python integers can exceed int64 and need the same clipping.
    Normalized float input retains libsndfile's existing quantization behavior.
    """
    arr = _sample_array(samples) if subtype in _SUBTYPE else _np.asarray(samples)
    if (spec := _SUBTYPE.get(subtype)) is not None and not _np.issubdtype(
        arr.dtype, _np.floating
    ):
        width, _, scale = spec
        return cast(
            "NDArray[_np.generic]",
            _np.clip(arr, -scale, scale - 1).astype(
                _np.int16 if width == 2 else _np.int32
            ),
        )
    if _np.issubdtype(arr.dtype, _np.integer) and arr.dtype not in (
        _np.int16,
        _np.int32,
    ):
        return arr.astype(_np.int16)
    return arr


# ── stdlib + numpy path ────────────────────────────────────────────────────────


def _read_numpy(
    raw: bytes | bytearray | memoryview, nchannels: int, sampwidth: int, dtype: str
) -> Samples:
    code = _PCM_CODE.get(sampwidth)
    if code is None:
        raise ValueError(_bad_width(sampwidth, "8/16/32"))
    _check_pcm_frames(raw, nchannels, sampwidth)
    arr: NDArray[_np.generic] = _np.frombuffer(raw, dtype=code)
    if dtype == "int16":
        return _read_int16_numpy(arr, nchannels, sampwidth)
    if nchannels > 1:
        arr = arr.reshape(-1, nchannels)
    # PCM scaling is a power of two, so converting directly to the requested
    # float dtype has the same rounding as float64 normalization then casting.
    # Shape first so the single writable allocation owns its channel layout.
    flt: NDArray[_np.floating] = arr.astype(
        _np.float32 if dtype == "float32" else _np.float64
    )
    flt *= 1.0 / (1 << (8 * sampwidth - 1))
    if sampwidth == 1:  # 8-bit WAV is unsigned, centered on 128
        flt -= 1.0
    return flt


def _read_int16_numpy(
    arr: NDArray[_np.generic], nchannels: int, sampwidth: int
) -> NDArray[_np.int16]:
    """Copy PCM16 counts directly; other widths retain their normalized conversion."""
    if sampwidth == 2:
        if nchannels > 1:
            arr = arr.reshape(-1, nchannels)
        # The raw-byte view is read-only and can alias caller-owned storage.
        # Copy after reshaping to retain an owned, writable result in either shape.
        return cast("NDArray[_np.int16]", arr.copy())
    flt = (
        (arr.astype(_np.float64) - 128.0) / 128.0
        if sampwidth == 1
        else arr.astype(_np.float64) / float(1 << (8 * sampwidth - 1))
    )
    if nchannels > 1:
        flt = flt.reshape(-1, nchannels)
    return _f2i16_numpy(flt)


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
    arr = _sample_array(samples)
    nchannels = 1 if arr.ndim == 1 else arr.shape[1]
    _write_frames(fp, _quantize(arr, code, scale), nchannels, width, samplerate)


def _quantize(arr: NDArray[_np.generic], code: str, scale: int) -> bytes:
    """Little-endian PCM bytes of *arr*: floats clamped and scaled, ints clamped as-is."""
    ints = (
        _np.round(_np.clip(arr, -1.0, 1.0) * scale)
        if _np.issubdtype(arr.dtype, _np.floating)
        else arr
    )
    return _np.ascontiguousarray(
        _np.clip(ints, -scale, scale - 1).astype(code)
    ).tobytes()


# ── pure-stdlib `array` floor (16-bit PCM only) ────────────────────────────────


def _write_array(
    fp: PathOrFile, samples: Samples, samplerate: int, subtype: str
) -> None:
    if subtype != "PCM_16":
        raise ValueError(_bad_subtype(subtype))
    raw, nchannels = _pcm16(samples)
    _write_frames(fp, raw, nchannels, 2, samplerate)


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
