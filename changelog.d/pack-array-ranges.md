`unpack_array` and `unpack_array_portable` accept keyword-only element `count`
and byte `offset` arguments. Callers can decode headers, body regions, and
selected records without copying slices of the input. NumPy retains its
zero-copy views and backend-specific dtype behavior; portable decoding returns
owned Python lists with explicit range validation.
