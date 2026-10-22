Fix `partition` to freeze each predicate result to a boolean once, preventing mutable or side-effecting truth values from dropping or duplicating items across its two outputs.
