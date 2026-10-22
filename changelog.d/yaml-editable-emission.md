Fix two cases where `yaml.dumps`, `dump` and `dump_all` wrote a different value
than they were given. Strings containing NEL (U+0085) are now double-quoted with
an escape instead of single-quoted, where readers folded NEL into a space. Floats
from `parse` keep their original spelling only while it still denotes the same
number; exponent spellings that the backend would truncate (for example
`3.403832445554453e+16`) are written in the shortest exact form instead.
