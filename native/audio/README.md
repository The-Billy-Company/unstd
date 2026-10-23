# Reviewed libsndfile build and offline admission

This is release/provisioning tooling. No audio function imports these tools,
checks versions, hashes binaries, adds wrappers, or changes SoundFile routing.
The patched library is packaged in an explicit SoundFile wheel; it is not
vendored into the unstd wheel.

The source is the official libsndfile 1.2.2 release archive, SHA256
`3799ca9924d3125038880367bf1468e53a1b7e3686a934f098b7e1d286cdb80e`.
The authenticated [official fix](https://github.com/libsndfile/libsndfile/commit/474e4d328b1e6240b93ec6ed14efb7c6a44bee57)
addresses [CVE-2026-88386](https://github.com/libsndfile/libsndfile/issues/1150).
Its complete upstream patch is retained as `upstream-474e4d3.patch`, SHA256
`9be5ac68e236d2ff2011f68065eacbf1b6daf4f9aa37a9f21ac9d74135697cba`.

That commit targets a newer parser than released 1.2.2. The exact backport
changes only thirteen `read_bytes = header_read` spellings to retain the
release's `byte_count += header_read` accounting, then applies with zero fuzz.
The builder pins the adapted patch and both original/patched `common.c` hashes.
It retains all input bytes, build scripts, commands, CMake cache, compiler
identity/hash, static codec archive hashes, capabilities and resulting native
library/wheel hashes in the output artifact and `provenance.json`.

## Build a new artifact

The Python binding must be the locked SoundFile 0.14.0 with CFFI and NumPy.
CMake is an explicit absolute executable path (the lock contains 4.4.4), never
an implicit system override. The helper does not install into its environment.

On macOS, install the Xcode command line tools, `pkg-config`, and the Homebrew
development libraries `flac`, `libvorbis`, `libogg`, `opus`, `mpg123`, and `lame`.
On Debian/Ubuntu the corresponding development packages are `libflac-dev`,
`libvorbis-dev`, `libogg-dev`, `libopus-dev`, `libmpg123-dev`, and
`libmp3lame-dev`, with a C toolchain, make, patch, and pkg-config. Static Linux
archives must be built with position-independent code. Static builds retain
the original codec notices, including Debian package copyright files.

Download the source archive from the fixed URL below; the helper authenticates
it before extracting or executing any source:

```console
curl --fail --location --proto '=https' --output /tmp/libsndfile-1.2.2.tar.xz https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz
uv run --no-sync python -m tests.tooling.runtime.audio_build --source /tmp/libsndfile-1.2.2.tar.xz --patch native/audio/upstream-474e4d3.patch --output .local/audio-security/build --cmake /absolute/path/to/cmake --static-codecs --jobs 2 --trust-policy .local/audio-security/trusted-build.json
```

`--prefix /opt/homebrew` is useful on macOS. The output directory and optional
trust-policy path must be new. A failed build never emits a trust policy.
The same-job policy option is for a trusted, reviewed build job using these
pinned inputs; it cannot admit an arbitrary existing provenance file. The
repository allowlist `reviewed-builds.json` remains empty until an exact
artifact is separately reviewed.

The static-codec option retains FLAC, Vorbis, Opus, LAME and mpg123 while
avoiding additional codec DSOs. Actual encode/decode probes cover all four
external formats. macOS builds verify that only system dependencies remain.
The local macOS qualification retained one libsndfile DSO plus libSystem.
The rebuilt wheel explicitly targets the build host's macOS major release,
rather than reusing CPython's older configured deployment target. Linux wheels
use a host tag, with no manylinux claim. Windows loader admission is implemented
but its native build/provisioning path still needs platform qualification.

## Install and admit in an isolated environment

After syncing the locked dependencies into an isolated validation environment,
verify the wheel against `output.wheel_sha256` from the trusted build receipt,
then install the exact local wheel with dependencies already satisfied:

```console
uv pip install --python /absolute/isolated/bin/python --no-deps --force-reinstall .local/audio-security/build/soundfile-0.14.0+unstd.474e4d3-py3-none-linux_x86_64.whl
/absolute/isolated/bin/python -m tests.tooling.runtime.audio --provenance .local/audio-security/build/provenance.json --trust-policy .local/audio-security/trusted-build.json --json
/absolute/isolated/bin/python -m pytest tests/suites/product/audio -q
```

Use the actual emitted wheel filename on other platforms. Retain the complete
source/build artifact, licenses and provenance with any redistributed wheel.
Compatibility and timing qualification applies to that exact binary and host;
static linking removes extra codec loads but does not prove zero startup or
per-call overhead across all platforms.

A later `uv sync` can restore the registry SoundFile wheel from the lock.
Reinstall the qualified wheel and rerun admission after dependency syncing;
a Python dependency floor alone cannot select the patched native library.

The offline preflight takes the live CFFI `sf_version_string` address and asks
the platform loader (`dladdr` or Windows module APIs) for its actual DSO. It
hashes that file and matches it to a separately trusted manifest digest and
reviewed source/fix evidence. SoundFile's `_full_path`, a matching version
string, or an untrusted wheel's own JSON cannot bypass admission. Installation
and policy files must be immutable/trusted during validation and deployment.
Run admission in a fresh process immediately after trusted provisioning. The
loader supplies an image path and the tool hashes that file; it does not hash
resident mapped pages or detect a hostile replacement of files after a library
was loaded. Never use it to bless a process that loaded libraries before an
untrusted installation changed. Static codec archives are copied into the
private build inputs before hashing and linking, so package-manager updates
cannot switch the linker to different archive bytes during the build.
There is no general version bypass, network vulnerability lookup, or automatic
trust in a manifest shipped alongside an arbitrary downloaded wheel.

The required audio security workflow builds from pinned inputs, installs its
exact wheel, runs this gate and exercises the audio contracts. Downstream
deployments must retain the same wheel and trusted receipt and run the
preflight with the interpreter that will execute audio work.
