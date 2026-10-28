Speed up `math.within` with scalar validation, shared unsigned views and a fused
64-bit integer scan in compiled wheels. Preserve exact fallback behavior for
mixed signedness, byte order, array tolerances and arbitrary integer widths.
