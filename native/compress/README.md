# Native gzip reads

[`src/`](src/README.md) owns the CPython adapter and streaming inflater.
[`vendor/`](vendor/README.md) holds authenticated upstream source closures;
[`experiments/`](experiments/README.md) holds isolated qualification tools and
review manifests. These are private build inputs; callers use
`unstd.compress.gzipx`.

`src/adapter.c` decodes eligible immutable gzip buffers with libdeflate. The native
kernel validates the gzip frame, CRC32, original size and consumed input before
returning bytes. It returns `None` for unsupported inputs or failed validation so
the Python wrapper can preserve its existing streaming and diagnostic behavior.
The last four input bytes provide an allocation hint; an absolute 64 MiB limit
and a compressed-size expansion budget bound speculative allocation.

[`vendor/libdeflate/`](vendor/libdeflate/) contains the unmodified source closure for gzip
decompression from [libdeflate 1.25](https://github.com/ebiggers/libdeflate/tree/v1.25).
Its [MIT license](vendor/libdeflate/COPYING) is included. Compression kernels, zlib
framing, CLI programs and build tooling are omitted. The archive URL, SHA256 and
individual source hashes are recorded in [UPSTREAM.json](vendor/libdeflate/UPSTREAM.json).
The ordinary extension build compiles these C sources directly; no system
libdeflate library, download or separate build tool is required.

`src/stream.c` retains compressed input and inflates directly into a Python byte
buffer within the caller's output budget. `src/inflate.c` calls the native zlib C
ABI; it makes no Python stdlib compression calls. macOS links its platform
`libz`, whose ARM64 inflater was faster on Billy's measured workloads. Other
platforms compile the unmodified [zlib 1.3.2](https://zlib.net/) inflation source
closure in [`vendor/zlib/`](vendor/zlib/) with `Z_PREFIX` to isolate its symbols. The
[license](vendor/zlib/LICENSE), archive checksum and individual source hashes are
included in [UPSTREAM.json](vendor/zlib/UPSTREAM.json). No Rust toolchain is required.

The Python reader retains its interpreter's gzip framing, buffering and error
delivery while selecting the compiled inflater and libdeflate CRC32 function.

## Isolated libdeflate 1.26 qualification

Default source and compiler settings remain on 1.25. The reviewed 1.26 release
improves CRC32 selection on Arm Neoverse V-series CPUs, but adds a first-use
sysfs open/read on Linux ARM64 to identify those processors. It is not an
advertised security fix and cannot satisfy a universal no-added-setup-overhead
condition. Existing compiled candidates and source identities remain useful
for explicit deployment qualification.

Download and extract the official release separately. The release archive's
SHA256 is `125856d4656e0feab660f94842f835923410c9281fedbcee64598c918da42b5a`,
checked against its upstream GitHub asset digest. Then run:

```console
python native/compress/experiments/qualify_libdeflate.py \
  --source /absolute/path/to/libdeflate-1.26 \
  --output /absolute/path/to/new-qualification-directory
```

The tool authenticates the fixed [review manifest](experiments/libdeflate-1.26.json),
copies only its 24 source/license files into a new private tree, checks every
copied hash, and builds only a native extension. It never changes the default
source tree or produces a release wheel. GNU C11 preserves Linux POSIX feature
declarations; Windows uses `/std:c11` and requires MSVC 2019 v16.8 or later.
The output records the exact reviewed manifest, compiled binary hash/size and
build paths. Build dependencies must already be installed; no downloads or
dependency resolution occur. A changed review or source file fails closed.

## Local zlib-rs qualification

An optional build-only experiment can substitute a separately reviewed
zlib-rs static library beneath the existing `inflate.c` ABI. The default build
still links platform zlib on macOS and compiles the authenticated zlib closure
elsewhere. No runtime dispatch, Cargo dependency resolution, download, wrapper,
new gzip framing or per-read version check is added. Libdeflate still handles
whole-buffer decoding and IEEE CRC32; zlib-ng writes are unchanged.

Build upstream [zlib-rs 0.6.7](https://github.com/trifectatechfoundation/zlib-rs/tree/v0.6.7)
in a separate directory after reviewing its source, license and lockfile. The
archive must export the custom prefix `unstd_zlib_rs_`, preventing accidental
binding to the interpreter's libz. For example, from a reviewed upstream tree:

```console
LIBZ_RS_SYS_PREFIX=unstd_zlib_rs_ cargo rustc --locked --offline --release \
  --manifest-path libz-rs-sys-cdylib/Cargo.toml --lib \
  --no-default-features --features std,custom-prefix \
  -- --print=native-static-libs
```

Keep the reported native-static-libs arguments, the complete source/toolchain
identities, and an independently reviewed SHA256 for the resulting static
archive. Supply its absolute path and digest during the local extension build:

```console
UNSTD_GZIP_ZLIB_RS=/absolute/path/to/libz_rs.a \
UNSTD_GZIP_ZLIB_RS_SHA256=EXPECTED_REVIEWED_ARCHIVE_SHA256 \
UNSTD_GZIP_ZLIB_RS_LINK_ARGS='-lSystem -lc -lm' \
python -m build --wheel --no-isolation
```

The link arguments above were reported by the macOS arm64 build; use the
arguments from the actual target build. Windows static archives use `.lib`.
The [recorded experiment provenance](experiments/zlib-rs-0.6.7.json) includes
the source archive, exact tag/commit, Cargo.lock/source hashes, toolchain, flags
and the observed static archive hash. It identifies that local experiment;
source paths and toolchains can change a rebuilt archive's hash. Independently
review each new artifact before supplying its expected digest.
Each architecture in a universal build must be present in the supplied archive.
Do not use `target-cpu=native` for portable artifacts. Missing/incorrect digests
and explicitly selected build failures fail rather than silently selecting the
default inflater. The zlib ABI version string remains a compatibility version,
not proof of the zlib-rs release; retain the source and archive provenance.
The hook copies the supplied archive into its private build directory, verifies
that copy's digest, and passes only the verified copy to the linker. Replacing
the original path after verification cannot change what is linked.

This experiment is rejected in the `CIBUILDWHEEL=1` release lane. Promoting it
to published wheels requires its source/license/SBOM closure and every platform's
qualification. Supplying a hash establishes archive identity, not trust in an
unreviewed library. This source tree does not vendor or resolve zlib-rs, and the
default offline supply policy remains unchanged.
Keep qualification wheels local; distribution also requires including the
reviewed zlib-rs license and its authenticated dependency/provenance closure.

Both backends receive the same checked Python RAW allocation callbacks, borrowed
input/output and output budget. Preserve bounded reads, EOF/unused input,
reentrant callbacks, concatenations, seek and malformed-stream delivery in tests.
Rust has a smaller manual-memory boundary but remains susceptible to bugs and
resource exhaustion; its historical stack-overflow advisory was fixed before
0.6.7. Early local timing found bounded inflater regressions, so it remains an
experiment rather than a default acceleration.
