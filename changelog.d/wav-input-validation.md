Reject nonpositive WAV block sizes before reading input, preventing zero-size
nonprogress and negative-size whole-file reads. Fallback whole-file decoders
also reject declared trailing partial PCM frames instead of silently dropping
their bytes.
