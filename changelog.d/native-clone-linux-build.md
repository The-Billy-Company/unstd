## Fix native clone builds on Linux

The native clone extension now builds with glibc headers, whose process-level
`clone` declaration conflicted with the extension's internal C function name.
The Python `clone` method and cloning behavior are unchanged. Strict native
builds no longer fail, and ordinary Linux source builds retain clone acceleration
instead of silently falling back to Python.
