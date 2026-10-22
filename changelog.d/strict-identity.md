### Added

- `jsonx.canonical_strict` encodes a defined JSON identity domain using a frozen
  version-1 byte format. It rejects implicit object/string conversions, non-string
  keys, subclasses, non-finite floats, surrogate code points and cycles. The legacy
  `canonical` function and its persisted bytes remain unchanged.
