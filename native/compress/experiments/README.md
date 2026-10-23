# Isolated gzip qualification

[`libdeflate-1.26.json`](libdeflate-1.26.json) authenticates the candidate source
closure consumed by [`qualify_libdeflate.py`](qualify_libdeflate.py).
[`zlib-rs-0.6.7.json`](zlib-rs-0.6.7.json) records reviewed local archive and
build provenance. Neither candidate changes the default source closure.

Run the qualifier from the repository root:

```console
python native/compress/experiments/qualify_libdeflate.py --source /absolute/path/to/libdeflate-1.26 --output /absolute/path/to/new-output
```

See the [gzip guide](../README.md) for review, build, and promotion requirements.
