Add `serde.jsonx.iterload` to process top-level JSON-array exports with bounded
reads and memory proportional to the chunk and largest record. Text, binary,
gzip and short-read streams work without new dependencies; UTF-8/16/32 binary
encoding detection and per-record decoder hooks are supported. Exhausting the
iterator validates the closing bracket and trailing input; callers retain
ownership of the source, and error positions identify the current item/chunk.

The native default path decodes bounded batches, retains UTF-8 binary input as
bytes, and constructs each batch with one native buffer allocation. Custom
decoder hooks remain lazy per item; refused or malformed batches use the
portable walker with preserved framing state and no callback replay.
