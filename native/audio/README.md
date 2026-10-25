# Reviewed libsndfile builds

Provisioning inputs for a patched SoundFile wheel, separate from unstd's native
extensions. Audio calls do not import the build or admission tools.

[`audio_build.py`](../../tests/tooling/runtime/audio_build.py) authenticates
libsndfile 1.2.2 and [`upstream-474e4d3.patch`](upstream-474e4d3.patch), backports
the unaligned-store fix with zero patch fuzz, and verifies `common.c` before and
after patching. Source, patch and resulting source hashes are pinned in
[`audio.py`](../../tests/tooling/runtime/audio.py). The build retains inputs,
commands, compiler identity, codec probes and binary hashes in `provenance.json`.

Use the locked SoundFile 0.14.0 binding, CFFI and NumPy, an absolute CMake executable,
a C toolchain, make, patch, pkg-config, and development libraries for FLAC,
Vorbis, Ogg, Opus, mpg123 and LAME. `--static-codecs` also requires their static
archives and license notices; Linux archives must be position-independent.

```console
curl --fail --location --proto '=https' --output /tmp/libsndfile-1.2.2.tar.xz https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz
uv run --no-sync python -m tests.tooling.runtime.audio_build \
  --source /tmp/libsndfile-1.2.2.tar.xz --patch native/audio/upstream-474e4d3.patch \
  --output .local/audio-security/build --cmake /absolute/path/to/cmake \
  --static-codecs --jobs 2 --trust-policy .local/audio-security/trusted-build.json
```

Output and trust-policy paths must be new. `--prefix /opt/homebrew` selects a
macOS dependency prefix. The helper builds without installing; macOS wheels
target the host's major release, Linux wheels use a host tag. Windows provisioning
still needs qualification. Preserve inputs, licenses and provenance with the wheel.

Only a trusted build job may issue its own policy. For other artifacts, review
the exact provenance through a separate trusted channel; the repository
[`reviewed-builds.json`](reviewed-builds.json) currently admits none. Verify the
emitted wheel against the trusted receipt's `output.wheel_sha256`, then install
that exact wheel in an isolated environment with dependencies already satisfied:

```console
uv pip install --python /absolute/isolated/bin/python --no-deps --force-reinstall /absolute/path/to/emitted.whl
/absolute/isolated/bin/python -m tests.tooling.runtime.audio \
  --provenance .local/audio-security/build/provenance.json \
  --trust-policy .local/audio-security/trusted-build.json --json
/absolute/isolated/bin/python -m pytest tests/suites/product/audio -q
```

Admission resolves the live CFFI `sf_version_string` address through the loader,
hashes its DSO file, and checks it against separately trusted provenance. Run in
a fresh process after trusted provisioning, with installation and policy files
held stable: this hashes the file, not resident pages. A later `uv sync` can
restore the registry wheel; reinstall and re-admit afterward. The
[audio security workflow](../../.github/workflows/audio-security.yml) exercises
this build, install and admission sequence.
