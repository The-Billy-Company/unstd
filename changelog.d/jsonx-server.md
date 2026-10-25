JSON release wheels now require active native JSON, float, ASCII, identity and
NDJSON paths during installed-wheel qualification. Performance CI retains paired
installed JSON record timings on Linux and macOS and rejects wall-time
regressions above 15%. The JSON throughput driver can require native dispatch
and enforce an explicit slowdown limit while preserving raw evidence.
