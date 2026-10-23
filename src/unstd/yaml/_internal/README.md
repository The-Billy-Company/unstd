# Private YAML implementation

`engine.py` owns parser constructors, tag and duplicate-key policy, alias
handling, and editable emission. `values.py` checks the native value domain
without expanding aliases. Public type aliases and codec functions stay in
`unstd.yaml`; its `_engine` boundary still controls optional-backend availability.
