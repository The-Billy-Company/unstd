"""TOML parser differential checks and independent writer models.

Envelope: byte 0 modulo 2 selects raw UTF-8 (invalid octets become surrogates)
or a generated TOML-1.0 model. Remaining bytes are source text or model entropy.
Raw reads compare values and precise stdlib exception type/message, including
binary load and caller ownership. Generated models check independent known
values, writer/parser round trips, stream writes and retained comments. Writer
checks run only when the explicit ``_HAVE_TOMLKIT`` capability is true; raw read
and generated read laws always run in base installations.
"""

from __future__ import annotations

import io
import tomllib

from unstd import toml


def _outcome(operation, *args):
    try:
        # repr preserves TOML's native scalar types while making NaNs comparable.
        return ("value", repr(operation(*args)))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, RecursionError) as error:
        return ("error", type(error), str(error))


def fuzz(data: bytes) -> None:
    """Check read fidelity and genuine writer behavior when that extra exists."""
    payload = data[1:]
    if data and data[0] % 2 == 0:
        text = payload.decode("utf-8", errors="surrogateescape")
        assert _outcome(toml.loads, text) == _outcome(tomllib.loads, text)
        source = io.BytesIO(payload)
        assert _outcome(toml.load, source) == _outcome(
            tomllib.load, io.BytesIO(payload)
        )
        assert not source.closed
        return
    numbers = list(payload[:64])
    expected = {
        "payload": payload.hex(),
        "count": len(payload),
        "enabled": bool(sum(payload) % 2),
        "numbers": numbers,
        "nested": {
            "low": -int.from_bytes(payload[:8], "little"),
            "text": 'line\n\t"\\',
        },
    }
    text = (
        '# fuzz-preserved-comment\npayload = "' + payload.hex() + '"\n'
        f"count = {len(payload)}\nenabled = {str(expected['enabled']).lower()}\n"
        f"numbers = {numbers!r}\n[nested]\nlow = {expected['nested']['low']}\n"
        'text = "line\\n\\t\\"\\\\"\n'
    )
    assert toml.loads(text) == expected
    if toml._HAVE_TOMLKIT:
        encoded = toml.dumps(expected)
        assert tomllib.loads(encoded) == expected
        stream = io.StringIO()
        toml.dump(expected, stream)
        assert not stream.closed
        assert tomllib.loads(stream.getvalue()) == expected
        edited = toml.parse(text)
        assert edited == expected
        retained = toml.dumps(edited)
        assert "# fuzz-preserved-comment" in retained
        assert tomllib.loads(retained) == expected
