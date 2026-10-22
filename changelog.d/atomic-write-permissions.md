Create atomic-write scratch files with the existing destination's permissions
from the outset, preventing retained read descriptors from exposing replacement
secrets. Copy permissions through the acquired descriptor where supported so a
replaced scratch path cannot redirect chmod onto an unrelated file.
