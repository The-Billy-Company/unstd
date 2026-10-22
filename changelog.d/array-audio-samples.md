The pure-stdlib WAV writer now validates every multichannel sample with the
same numeric check used for mono input. Numeric strings are rejected instead
of being silently converted to integer PCM values. Rejected chunks emit no
bytes, and an incremental writer remains usable afterward.
