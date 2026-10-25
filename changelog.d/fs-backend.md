Filesystem traversal reduces path construction and directory ownership overhead
while retaining descriptor-relative symlink protection and cleanup on early
exit or failure. Existing-directory checks avoid a failed mkdir, and strict
atomic writes skip redundant parent creation after verifying the directory.
File syncing, permissions, and directory-sync acknowledgments are preserved.
