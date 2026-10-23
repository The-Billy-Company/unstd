# Default C text kernels

[`_text_native.c`](_text_native.c) implements bounded Levenshtein distance and
Jaro similarity behind the `unstd.text._text_native` extension. Exact-type,
Unicode, allocation, and work-budget checks preserve the Python wrapper's
fallback protocol. The opt-in alternative lives in [`../rust/`](../rust/README.md).
