Added an isolated libdeflate 1.26 qualification builder with a fixed reviewed
manifest, copied source/license hash checks and C11 compilation. Default
libdeflate 1.25 and compiler settings remain unchanged: 1.26 adds first-use
Linux ARM64 CPU-identification syscalls and does not establish universal savings.

Added a local build-only zlib-rs inflater qualification option using a reviewed,
privately prefixed static archive and mandatory SHA256. Default platform/vendored
zlib, gzip framing, checked Python allocation callbacks and compiler-free
fallbacks remain intact. The experiment is blocked from release wheel builds
pending full source/license/SBOM and platform qualification.
