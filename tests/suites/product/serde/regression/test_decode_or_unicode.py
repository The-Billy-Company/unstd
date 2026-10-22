r"""``Codec.decode_or`` treats non-UTF-8 payload text as a cache miss.

Original failure: ``Codec(str).decode_or(b'"\xff"')`` raised the
``UnicodeDecodeError`` msgspec emits for invalid UTF-8 inside a JSON string,
crashing the "malformed payload -> default" reader. RFC 8259 requires UTF-8, so
these payloads are malformed; a hook's own errors must still propagate.
"""

from __future__ import annotations

import pytest

from tests.support.backends import require_backend


msgspec = require_backend("msgspec")
from unstd.serde import structs  # noqa: E402


class Opaque:
    pass


class Holder(msgspec.Struct):
    value: Opaque


@pytest.mark.parametrize("raw", [b'"\xff"', b'"\xc3("', '"\ud800"'])
def test_non_utf8_payload_returns_default(raw: bytes | str) -> None:
    codec = structs.Codec(str)
    assert codec.decode_or(raw, "miss") == "miss"
    assert codec.decode_or(b'"ok"', "miss") == "ok"


def test_hook_failures_other_than_validation_still_propagate() -> None:
    def hook(kind: type, obj: object) -> object:
        raise LookupError(obj)

    with pytest.raises(LookupError):
        structs.Codec(Holder, dec_hook=hook).decode_or(b'{"value":"x"}', None)
