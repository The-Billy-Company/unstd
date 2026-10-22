`fs.atomic_writer` now rejects modes other than `"w"` and `"wb"` before creating
directories or scratch files. Previously, unsupported read and exclusive-create
modes could silently replace an existing destination with an empty file.
