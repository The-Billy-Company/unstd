Portable array packing and the base-install fallback avoid scalar boxing for
matching one-dimensional integer and float64 arrays. Exact stdlib arrays need
no extra; exact NumPy arrays also support logical one-dimensional strides and
byte-order conversion. Struct validation remains authoritative for other
inputs, including float16/float32 NaN conversion and custom iterators.
