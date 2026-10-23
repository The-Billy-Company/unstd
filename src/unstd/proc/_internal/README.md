# Private process implementation

`results.py` owns captured outcomes and process errors. The public
`unstd.proc` module owns execution, validation, timeout handling, and logging.
Result classes keep their `unstd.proc` identity for existing imports and pickles.
