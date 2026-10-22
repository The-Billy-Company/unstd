Finish partial NDJSON writes before counting a record or consuming the next one.
A zero write count, or None from a standard io.IOBase stream, now raises
BlockingIOError instead of silently reporting success with missing data. Generic
write-only sinks may still return None to acknowledge complete writes. Existing
sink exceptions and caller ownership remain intact.
