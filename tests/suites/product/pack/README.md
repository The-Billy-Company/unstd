# Binary packing contracts

Contracts for `unstd.pack.binpack`: stdlib wire formats and compatible array packing on NumPy and stdlib implementations.

- [unit/](unit/README.md): struct parity, array formats, buffer handling, and backend agreement.
- [properties/](properties/README.md): generated struct formats, offsets, and truncated inputs.
- [regression/](regression/README.md): dtype spelling and buffer byte-size defects.

Scalar contracts and forced fallback tests run without extras. NumPy array-path cases run when the `pack` capability is installed.

Run from the repository root:

```console
uv run --no-sync pytest tests/suites/product/pack -q
```

See [the module documentation](../../../../src/unstd/pack/README.md) for the public contract and [the test guide](../../../README.md) for environment setup.
