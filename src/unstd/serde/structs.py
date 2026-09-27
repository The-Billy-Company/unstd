"""Typed msgspec codecs — one reusable Encoder/Decoder per wire schema.

Where ``jsonx`` handles the untyped ``dict``/``list`` round-trips stdlib ``json``
did, this module formalizes the *typed hot seam*: a payload with a fixed schema
(a ``msgspec.Struct``) that must validate-on-decode. It standardizes the pattern
every hand-rolled site re-rolled — build one ``msgspec.json.Encoder`` + one
``msgspec.json.Decoder`` per schema and reuse them (msgspec's own perf guidance:
constructing a codec is comparatively expensive, encode/decode is not) — behind a
small generic :class:`Codec` — construct one per schema at import time and
share it::

    USER = Codec(User)
    USER.decode(raw)

Backend: unlike ``jsonx``/``b64``/``ndjson``, typed codecs have **no
stdlib equivalent**, so this module hard-requires ``msgspec`` (the ``serde``
extra). Imported without it, it raises a clear ImportError pointing at
``pip install 'unstd[serde]'`` rather than a bare ``ModuleNotFoundError``.

Faithfulness contract — a :class:`Codec` is a *thin* wrapper: :meth:`Codec.encode` /
:meth:`Codec.decode` forward straight to the underlying msgspec objects, so the
emitted bytes are byte-for-byte what ``msgspec.json.Encoder(...).encode(...)``
produces (compact UTF-8, no inter-token whitespace) and ``decode`` validates against
the schema exactly as msgspec does. The only added surface is :meth:`Codec.decode_or`,
which encodes the "malformed payload → cache miss" convention every Redis/HTTP cache
reader shares: catch ``msgspec.DecodeError`` (``ValidationError`` is a subclass, so a
typed-wrong field is caught too) and return a default; any *other* exception
propagates untouched.

:func:`float_enc_hook` is the shared ``enc_hook`` for structs whose numeric values
arrive as non-native scalars — ``numpy`` ``float32``/``float64``, ``Decimal`` — which
msgspec routes to ``enc_hook`` because it dispatches on the *exact* type. It coerces
anything ``float()``-able to ``float`` (so a numpy-scalar-bearing struct serializes
identically to one built from native ``float``) and raises on a genuinely
unencodable value, so a nan-in-the-map bug surfaces rather than silently corrupting
the wire.

Every other msgspec constructor knob is forwarded straight through, not
reinvented: ``order`` gives the typed path the same canonical-bytes capability
``jsonx.canonical()`` has for untyped JSON (``"deterministic"`` sorts dict
keys/set elements; ``"sorted"`` also sorts struct fields by name), plus
``decimal_format``/``uuid_format`` on encode and ``strict``/``float_hook`` on
decode.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, SupportsFloat, final


try:
    import msgspec
except ImportError as exc:  # typed codecs have no stdlib equivalent
    msg = "unstd.serde.structs requires the 'serde' extra — install with: pip install 'unstd[serde]'"
    raise ImportError(msg) from exc


EncHook = Callable[[object], object]
DecHook = Callable[[type, object], object]
FloatHook = Callable[[str], object]
Order = Literal["deterministic", "sorted"] | None

__all__ = [
    "Codec",
    "DecHook",
    "EncHook",
    "FloatHook",
    "Order",
    "float_enc_hook",
]


def float_enc_hook(obj: object) -> float:
    """Coerce a non-native numeric scalar (numpy ``float32``/``float64``, ``Decimal``) to ``float``. msgspec dispatches on the *exact* type — routing any ``float`` subclass here — so anything ``float()``-able is accepted and anything else raises."""
    if isinstance(obj, SupportsFloat):
        return float(obj)
    msg = f"unencodable value of type {type(obj).__name__}"
    raise TypeError(msg)


@final
class Codec[T]:
    """A reusable typed JSON codec for one wire schema ``T`` (usually a ``msgspec.Struct``).

    Holds one ``msgspec.json.Encoder`` + one ``msgspec.json.Decoder``, built once and
    reused for the object's life — the whole point. :attr:`encode` / :attr:`decode`
    *are* the msgspec methods, bound once at construction, so a call pays no
    forwarding frame on the hot path.
    """

    __slots__ = ("decode", "encode")

    encode: Callable[[T], bytes]
    """Serialize a value to compact UTF-8 JSON bytes."""
    decode: Callable[[bytes | str], T]
    """Decode + validate into a ``T``; raises ``msgspec.DecodeError`` on a malformed or schema-invalid payload."""

    def __init__(
        self,
        spec: type[T],
        *,
        enc_hook: EncHook | None = None,
        dec_hook: DecHook | None = None,
        order: Order = None,
        decimal_format: Literal["string", "number"] = "string",
        uuid_format: Literal["canonical", "hex"] = "canonical",
        strict: bool = True,
        float_hook: FloatHook | None = None,
    ) -> None:
        """Initialize the instance.

        ``order="deterministic"`` sorts dict keys / set elements so equal values
        always encode identically (comparison/hashing the encoded bytes);
        ``"sorted"`` additionally sorts struct/dataclass fields by name — msgspec's
        own maintainer benchmarks this ~20-25% faster than routing the same struct
        through ``jsonx.dumps(..., sort_keys=True)`` for a canonical-bytes need,
        since it never leaves the typed encoder. ``strict=False`` widens decode
        coercion (e.g. a JSON string where the schema expects an int) — msgspec's
        own escape hatch for loose upstream producers.
        """
        self.encode = msgspec.json.Encoder(
            enc_hook=enc_hook,
            order=order,
            decimal_format=decimal_format,
            uuid_format=uuid_format,
        ).encode
        decoder: msgspec.json.Decoder[T] = msgspec.json.Decoder(
            spec, dec_hook=dec_hook, strict=strict, float_hook=float_hook
        )
        self.decode = decoder.decode

    def decode_or(self, raw: bytes | str, default: T | None = None) -> T | None:
        """Decode ``raw``, or return ``default`` when it is malformed / schema-invalid (the "malformed → cache miss" convention). Only ``msgspec.DecodeError`` (``ValidationError`` included) is swallowed; any other exception propagates."""
        try:
            return self.decode(raw)
        except msgspec.DecodeError:
            return default
