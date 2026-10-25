# Serialization internals

`json_encode.py` owns exact-output eligibility proofs, bounded snapshots and
reusable stdlib encoders. `json_common.py` retains Python recursion headroom.
The public `jsonx.py` keeps codec entrypoints, backend selection and native
dispatch bindings. Historical `_json_encode` and `_json_common` paths remain
readable by diagnostics. Compiled source belongs under `native/serde/src/`;
its underscored extension and typing stub retain their existing import path.
