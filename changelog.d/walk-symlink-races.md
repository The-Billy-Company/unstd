Protect default filesystem enumeration against directory and ancestor symlink
replacement on platforms supporting descriptor-relative no-follow traversal.
Bound live handles by traversal depth and close them on termination. Preserve
the portable fallback with directory-identity checks and document its weaker
concurrency guarantees and the limits of returned path names.
