"""Bounded RIFF/PCM16 decoding, streaming, wrapping and writing laws.

Envelope: byte 0 modulo 3 selects generated PCM16, raw RIFF bytes, or a generated
invalid header; byte 1 selects 1..4 channels and a common sample rate. Remaining
bytes are complete little-endian PCM16 frames (mode 0), a whole raw WAV (mode 1),
or PCM bytes plus a fixed corruption selected by byte 1 (mode 2).

Raw mode dispatches RIFF/WAVE only: foreign formats, oversized or inconsistent
chunk extents, channels >8 and declared frame/sample allocation >64 KiB are preflight refusals
in this harness, before any native read. Valid complete PCM16 raw files compare
against wave only when their PCM format fields are internally consistent.
Other bounded RIFF files still exercise decode/error handling,
but broader soundfile format support is not mistaken for wave parity. Generated
files check exact PCM bytes, float scaling, blocks and both writer interfaces.
"""

from __future__ import annotations

import io
import struct
import wave

from unstd.audio import wavx


_LIMIT = 65536
_NATIVE_ERRORS = (wavx._sf.SoundFileRuntimeError,) if wavx._HAVE_SOUNDFILE else ()
_ERRORS = (wave.Error, EOFError, ValueError, OSError, struct.error, *_NATIVE_ERRORS)


def _list(samples):
    return samples.tolist() if hasattr(samples, "tolist") else list(samples)


def _pcm(raw, channels, rate):
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        writer.writeframes(raw)
    return output.getvalue()


def _bounded_riff(raw):
    if len(raw) < 12:
        return True
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return False
    riff_size = int.from_bytes(raw[4:8], "little")
    if riff_size > _LIMIT or riff_size < 4:
        return False
    offset = 12
    while offset + 8 <= len(raw):
        tag = raw[offset : offset + 4]
        length = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        if length > _LIMIT or offset + 8 + length > riff_size + 8:
            return False
        if tag == b"fmt " and offset + 24 <= len(raw):
            channels = int.from_bytes(raw[offset + 10 : offset + 12], "little")
            if channels > 8:
                return False
        if tag == b"data" and length > _LIMIT // 4:
            # float64 output is four times the PCM16 input size.
            return False
        offset += 8 + length + length % 2
    return True


def _shared_pcm16(raw, channels, rate):
    """Require valid PCM metadata that wave otherwise accepts without checking."""
    offset = 12
    valid_format = False
    data_chunks = 0
    while offset + 8 <= len(raw):
        tag = raw[offset : offset + 4]
        length = int.from_bytes(raw[offset + 4 : offset + 8], "little")
        if tag == b"fmt ":
            if valid_format or length < 16 or offset + 24 > len(raw):
                return False
            code, declared_channels, declared_rate, byte_rate, alignment, bits = (
                struct.unpack_from("<HHIIHH", raw, offset + 8)
            )
            valid_format = (
                code == 1
                and declared_channels == channels
                and declared_rate == rate
                and rate > 0
                and alignment == channels * 2
                and byte_rate == rate * alignment
                and bits == 16
            )
            if not valid_format:
                return False
        elif tag == b"data":
            data_chunks += 1
            if not valid_format or length % (channels * 2):
                return False
        offset += 8 + length + length % 2
    return valid_format and data_chunks == 1


def _check(raw, pcm, channels, rate):
    integers = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    flat = list(integers)
    shaped = (
        flat
        if channels == 1
        else [flat[i : i + channels] for i in range(0, len(flat), channels)]
    )
    for dtype in ("int16", "float32", "float64"):
        expected_flat = (
            flat if dtype == "int16" else [sample / 32768 for sample in flat]
        )
        expected = (
            expected_flat
            if channels == 1
            else [
                expected_flat[i : i + channels] for i in range(0, len(flat), channels)
            ]
        )
        source = io.BytesIO(raw)
        samples, actual_rate = wavx.read(source, dtype=dtype)
        assert actual_rate == rate
        assert _list(samples) == expected
        assert not source.closed
        chunks = []
        source = io.BytesIO(raw)
        for block in wavx.blocks(source, 1 + len(pcm) % 31, dtype=dtype):
            chunks.extend(_list(block))
        assert chunks == expected
        assert not source.closed
    assert wavx.wrap_pcm(pcm, rate, channels=channels) == _pcm(pcm, channels, rate)
    for streaming in (False, True):
        output = io.BytesIO()
        if streaming:
            with wavx.Writer(output, rate, channels=channels) as writer:
                split = len(shaped) // 2
                if shaped[:split]:
                    writer.write(shaped[:split])
                if shaped[split:]:
                    writer.write(shaped[split:])
        else:
            # Empty multichannel arrays cannot encode their shape as Python lists.
            if not shaped and channels > 1:
                continue
            wavx.write(output, shaped, rate)
        assert not output.closed
        with wave.open(io.BytesIO(output.getvalue()), "rb") as reader:
            assert reader.getparams()[:3] == (channels, 2, rate)
            assert reader.readframes(reader.getnframes()) == pcm


def fuzz(data: bytes) -> None:
    """Check PCM16 laws or dispatch one allocation-bounded raw RIFF."""
    mode = data[0] % 3 if data else 0
    selector = data[1] if len(data) > 1 else 0
    payload = data[2:]
    if mode == 1:
        if not _bounded_riff(payload):
            return
        try:
            with wave.open(io.BytesIO(payload), "rb") as reader:
                channels, width, rate, count = reader.getparams()[:4]
                if channels > 8 or count * channels * 8 > _LIMIT:
                    return
                pcm = reader.readframes(count)
                shared = (
                    width == 2
                    and len(pcm) == count * channels * 2
                    and _shared_pcm16(payload, channels, rate)
                )
        except (wave.Error, EOFError, ValueError, OSError, struct.error):
            shared = False
        if shared:
            _check(payload, pcm, channels, rate)
        else:
            try:
                samples, _ = wavx.read(io.BytesIO(payload), dtype="int16")
            except _ERRORS:
                pass
            else:
                # Broader supported PCM widths are legal; the decoder stays bounded.
                assert len(samples) <= _LIMIT
        return
    channels = 1 + selector % 4
    rate = (8000, 16000, 22050, 44100, 48000)[(selector // 4) % 5]
    frame_size = channels * 2
    pcm = payload[: min(len(payload), _LIMIT // 4) // frame_size * frame_size]
    raw = _pcm(pcm, channels, rate)
    if mode == 2:
        corruption = selector % 3
        if corruption == 0:
            raw = b"FAIL" + raw[4:]
        elif corruption == 1:
            raw = raw[:8]
        else:
            raw = raw[:22] + b"\0\0" + raw[24:]
        try:
            wavx.read(io.BytesIO(raw))
        except _ERRORS:
            pass
        else:
            msg = "unambiguously invalid WAV header was accepted"
            raise AssertionError(msg)
        return
    _check(raw, pcm, channels, rate)
