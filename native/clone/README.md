# Native structural cloning

[`src/`](src/README.md) builds the optional `unstd.clone._clone_native` extension
for CPython GIL builds. It copies exact builtin data and admitted dataclasses;
the [Python wrapper](../../src/unstd/clone/structural.py) owns copy-protocol
admission, shared memo semantics, and fallback. Use the
[public API](../../src/unstd/clone/README.md).
