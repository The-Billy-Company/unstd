Speed up gzip reads with the compression extra installed: file streams use the
running interpreter's stdlib reader, and complete single-member byte buffers use
zlib's C gzip parser. Native compression remains available. Preserve bounded
reads, seeking, concatenated members, header quirks, buffer slicing semantics
and exact stdlib decode diagnostics. Remove the unused native inflater adapter
and add paired, output-checked read workloads with source and dispatch evidence.
