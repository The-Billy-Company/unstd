"""The array floor rejects nonnumeric samples before emitting any bytes."""

from __future__ import annotations

import io
import wave

import pytest

from unstd.audio import wavx


@pytest.fixture(autouse=True)
def array_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wavx, "_HAVE_SOUNDFILE", False)
    monkeypatch.setattr(wavx, "_HAVE_NUMPY", False)
    monkeypatch.setattr(wavx, "_np", None)


@pytest.mark.parametrize("samples", [["7"], [["7", "8"]], [[1, 2], ["7", 8]]])
def test_array_write_rejects_nonnumeric_samples_before_output(
    samples: list[object],
) -> None:
    sink = io.BytesIO()
    with pytest.raises(TypeError, match="expected a numeric sample, got str"):
        wavx.write(sink, samples, 8000)  # type: ignore[arg-type]
    assert sink.getvalue() == b""
    assert not sink.closed


@pytest.mark.parametrize(
    ("channels", "samples"),
    [(1, ["7"]), (2, [["7", "8"]]), (2, [[1, 2], ["7", 8]])],
)
def test_array_writer_rejects_before_output_and_recovers(
    channels: int, samples: list[object]
) -> None:
    sink = io.BytesIO()
    good = [0.5] if channels == 1 else [[0.5] * channels]
    with wavx.Writer(sink, 8000, channels=channels) as writer:
        writer.write(good)
        before = sink.getvalue()
        with pytest.raises(TypeError, match="expected a numeric sample, got str"):
            writer.write(samples)  # type: ignore[arg-type]
        assert sink.getvalue() == before
        writer.write(good)
    sink.seek(0)
    with wave.open(sink, "rb") as reference:
        assert reference.getnchannels() == channels
        assert reference.getnframes() == 2
        raw = reference.readframes(3)
    assert raw == (16384).to_bytes(2, "little", signed=True) * (2 * channels)
