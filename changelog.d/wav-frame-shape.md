Fix WAV fallback writers accepting frames that contradict the channel layout. The
pure-stdlib floor now rejects ragged multichannel lists with `ValueError`, and both
stdlib fallbacks reject `Writer.write` chunks whose channel count differs from
the writer's `channels`, matching NumPy and soundfile instead of writing a data
chunk the header misdescribes.
