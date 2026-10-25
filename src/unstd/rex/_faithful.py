"""Compatibility imports for private regex helpers.

New internal code imports from ``unstd.rex._internal``. The historical names
remain importable for existing diagnostics and exception deserialization.
"""

from unstd.rex._internal.patterns import (
    LinearRegexError as LinearRegexError,
)
from unstd.rex._internal.patterns import (
    _absent_as_empty as _absent_as_empty,
)
from unstd.rex._internal.patterns import (
    _LinearPattern as _LinearPattern,
)
from unstd.rex._internal.patterns import (
    _StrictLinearPattern as _StrictLinearPattern,
)
from unstd.rex._internal.profile import (
    _faithful_classes as _faithful_classes,
)
from unstd.rex._internal.profile import (
    native_profile as native_profile,
)
