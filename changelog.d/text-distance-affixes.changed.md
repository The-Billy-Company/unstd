Edit distance on the base-install text paths now removes equal prefixes and
suffixes before calculating the remaining edits. Long, near-identical strings
avoid large bit-vector or matrix computations in both the bundled C/Rust kernels
and the pure-Python fallback. Results and subclass/sequence behavior stay the
same. Native scans and residual computations remain bounded; RapidFuzz's public
binding is unchanged. Rebuild native extensions to use the native improvement.
