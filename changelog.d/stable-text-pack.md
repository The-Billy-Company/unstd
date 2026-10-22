Added explicit backend-independent fuzzy scorers: `fuzz.indel_ratio` computes
normalized Indel similarity with or without RapidFuzz, and `fuzz.difflib_ratio`
always uses stdlib SequenceMatcher. `best_match`, `extract`, and `cdist` accept
`scorer=` to keep rankings and inclusive cutoff decisions stable as optional
dependencies change. Existing `ratio` and omitted-scorer behavior are retained.

Added `binpack.pack_array_portable` and `unpack_array_portable`, which always use
the portable dtype subset and stdlib struct conversion/error rules, returning
bytes and an owned list of Python scalars. Existing NumPy coercion, broader
dtypes, and zero-copy results remain available through the original helpers.
Clarified the historical backend differences in module documentation.

Public text and array-helper annotations now support `typing.get_type_hints`
without optional backends; portable dtype signatures use plain strings.

Explicit-scorer matching consumes mapping keys in iteration order and applies
identical inclusive-cutoff behavior across backends, including NaN and infinite
cutoffs. Historical omitted-scorer behavior remains unchanged.
