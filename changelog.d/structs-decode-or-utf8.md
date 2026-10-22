Fix `serde.structs.Codec.decode_or` so a payload whose JSON string content is not
valid UTF-8 (or a `str` payload carrying lone surrogates) returns the default as a
malformed cache miss, instead of leaking the `UnicodeError` msgspec raises for it.
`Codec.decode` is unchanged and still raises exactly what msgspec raises; hook
exceptions other than msgspec validation errors still propagate from `decode_or`.
