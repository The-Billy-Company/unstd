- Added `rand.Generator(seed, backend="stdlib")` for independent, explicitly
  selected random streams. Its default remains stdlib with NumPy installed;
  `backend="numpy"` requires the `rand` extra and selects PCG64 for bulk draws.
  Existing module-level defaults and valid seeded draw sequences remain
  unchanged. Reproducibility requires the same backend and call sequence.
- Failed NumPy-backed `seed()` calls now preserve both scalar and bulk state.
  Bulk helpers consistently reject negative/noninteger counts and reversed or
  empty intervals before drawing, including when requesting zero integers.
  Bulk floats also reject nonfinite bounds or spans without consuming state,
  including for zero draws; equal finite bounds remain valid.
- Random bulk result annotations now support runtime type introspection with
  and without NumPy.
