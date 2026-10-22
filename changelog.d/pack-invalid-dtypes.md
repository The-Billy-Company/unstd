The stdlib array-packing fallback now rejects empty and incomplete dtype strings
with `struct.error`, matching its other unsupported dtype errors, instead of
leaking an internal `IndexError`.
