# Focused performance profiles

These drivers compare saved and current implementations, check outputs and import provenance, and retain timings separately from profiling. Run `python -m bench.profiles.NAME --help`. The earlier `python -m bench.NAME` and `python bench/NAME.py` entry points forward here.

- [`serde_profile.py`](serde_profile.py)
- [`clone_ids_profile.py`](clone_ids_profile.py)
- [`native_profile.py`](native_profile.py)
- [`crypto_hash.py`](crypto_hash.py)
- [`billy_serde.py`](billy_serde.py)
- [`gzip_reads.py`](gzip_reads.py)
- [`text_native_compare.py`](text_native_compare.py)

See the [benchmark guide](../README.md).
