# Release verification tooling

[`verify.py`](verify.py) authenticates archive paths, metadata, RECORD entries,
licenses, vendored provenance, and the exact files handed to a publisher without
installing or executing archive contents. [`cache_key.py`](cache_key.py) identifies
reusable build/test dependencies; [`reuse_ci.py`](reuse_ci.py) checks recent
exact-commit CI evidence. [`wheel_tests.py`](wheel_tests.py) runs installed-wheel
contracts in bounded parallel workers.

```console
python -m tests.tooling.release verify --help
```

The [release gates](../../suites/assurance/gates/release/README.md) test these
instruments. Real source and wheel behavior belongs to the
[packaging plane](../../suites/assurance/planes/packaging/README.md).
