# Native dataset source

[`adapter.c`](adapter.c) implements checked row, batch, span, and full-offset
validation against a contiguous buffer. All offsets are little-endian uint64;
pointer arithmetic only follows checks against the acquired buffer length.
