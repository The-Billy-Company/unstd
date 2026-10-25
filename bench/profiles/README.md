# Focused performance profiles

These drivers compare saved and current implementations, check outputs and import provenance, and retain timings separately from profiling. Run `python -m bench.profiles.NAME --help`. The earlier `python -m bench.NAME` and `python bench/NAME.py` entry points forward here.

- [`serde_profile.py`](serde_profile.py)
- [`clone_ids_profile.py`](clone_ids_profile.py)
- [`clone_cpu.py`](clone_cpu.py) — paired wall and process CPU measurements, including large trees, preserved aliases and cyclic fallback
- [`clone_common_cpu.py`](clone_common_cpu.py) — public Python clone entrypoints on common nested data and dataclasses, against saved native baselines
- [`native_profile.py`](native_profile.py)
- [`crypto_hash.py`](crypto_hash.py)
- [`billy_serde.py`](billy_serde.py)
- [`billy_audio.py`](billy_audio.py) — exact PCM wrapping and raw normalization ports, paired timings, allocation peaks and captured Billy consumer source
- [`gzip_reads.py`](gzip_reads.py)
- [`text_native_compare.py`](text_native_compare.py)

See the [benchmark guide](../README.md).

Current `deep` workloads require shared-reference preservation; saved historical
baselines retain their earlier alias-duplication checks. `asdict` keeps its
separate-copy behavior. Saved native manifests can identify the graph copier
with `native_graph_entrypoint` so the installed kernel cannot leak into a
historical comparison.
