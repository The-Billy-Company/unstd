# Test environment and process ownership

[`anchors.py`](anchors.py) supplies repository, tests, and product-suite roots.
[`backends.py`](backends.py) distinguishes optional local capabilities from
required CI backends. [`process.py`](process.py) launches bounded Python workers,
preserves the selected source or installed package, and owns cleanup and reaping.

Use these shared mechanisms when a suite needs paths, backend admission, or a
child interpreter. Keep product assertions in their
[owning suites](../../suites/README.md).
