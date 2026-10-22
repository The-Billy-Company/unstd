Add required full-source SAST and duplicate-code checks to CI and releases:
Bandit, CodeQL security-extended for Python/C/C++/Rust/Actions, Rust Clippy, and
Pylint duplicate-code. Findings and incomplete scanner evidence fail the gate;
reports and diagnostics are retained. Consolidate strict Base64 decoding and
regex absent-group projection into shared implementations. Document scope,
local commands, reviewed false positives and finding remediation in SECURITY.md.
