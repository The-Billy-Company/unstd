### Added

- Filesystem writers and moves accept `durable="strict"`; `sync_dir` accepts
  `strict=True`. Strict mode reports directory-sync failures, preflights support,
  syncs new parent entries, and identifies failures after replacement as already
  committed. Existing best-effort defaults remain unchanged. Strict moves sync
  both directories; source-file content flushing remains the caller's job.
