# Isolated gzip qualification

These files qualify candidates without changing the default libdeflate 1.25 and
zlib build:

| File | Responsibility |
| --- | --- |
| [`qualify_libdeflate.py`](qualify_libdeflate.py) | Stage authenticated candidate files, compile an isolated extension, and emit `qualification.json` |
| [`libdeflate-1.26.json`](libdeflate-1.26.json) | Reviewed archive identity and hashes of the candidate's 24 retained files |
| [`zlib-rs-0.6.7.json`](zlib-rs-0.6.7.json) | Evidence for one source, lockfile, toolchain, flags, and static-archive build |

Qualification artifacts stay local until source, license, dependency provenance,
and platform behavior are reviewed for distribution.

## libdeflate 1.26

From the repository root, with the candidate already extracted and build
dependencies installed:

```console
python native/compress/experiments/qualify_libdeflate.py \
  --source /absolute/path/to/libdeflate-1.26 \
  --output /absolute/path/to/new-output
```

`qualify_libdeflate.py` authenticates the fixed review manifest, copies and hashes
its 24 files, and compiles only an extension in a new output directory outside
`native/`. It makes
no downloads or dependency-resolution calls. GNU C11 is selected on Unix;
Windows uses `/std:c11`. `qualification.json` records the review and binary
hashes, size and build paths. A changed manifest, source mismatch or build failure
aborts; no wheel is produced.

## zlib-rs 0.6.7

[`zlib-rs-0.6.7.json`](zlib-rs-0.6.7.json) records one local experiment's source,
lockfile, toolchain, flags and archive identity. Rebuilds need their own reviewed
archive digest. From a separately reviewed upstream tree:

```console
LIBZ_RS_SYS_PREFIX=unstd_zlib_rs_ cargo rustc --locked --offline --release \
  --manifest-path libz-rs-sys-cdylib/Cargo.toml --lib \
  --no-default-features --features std,custom-prefix \
  -- --print=native-static-libs
```

Supply that static archive and its independently reviewed digest to the local
extension build:

```console
UNSTD_GZIP_ZLIB_RS=/absolute/path/to/libz_rs.a \
UNSTD_GZIP_ZLIB_RS_SHA256=EXPECTED_REVIEWED_ARCHIVE_SHA256 \
UNSTD_GZIP_ZLIB_RS_LINK_ARGS='-lSystem -lc -lm' \
python -m build --wheel --no-isolation
```

Use the native-static-libs arguments reported by the target build; the example
is macOS arm64. Windows archives use `.lib`. The archive must export
`unstd_zlib_rs_` symbols and contain every requested architecture; avoid
`target-cpu=native` for portable artifacts.

The hook hashes a private copy and links only that copy. Invalid digests and
explicitly selected build failures abort; `CIBUILDWHEEL=1` rejects this experiment.
Only the streaming inflater changes: libdeflate decoding/CRC32 and gzip framing
stay in place. Preserve bounded reads, EOF/unused input, exporter reentrancy,
concatenations, seeking and malformed-stream error delivery when qualifying it.
