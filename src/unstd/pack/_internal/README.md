# Private binary packing implementation

`dtype.py` owns the portable numeric dtype grammar, aliases, byte-order rules,
and bounded parser/compiled-format caches. It uses only stdlib `struct`.
The public `binpack` module retains backend selection, array operations,
and direct stdlib aliases.
