"""Deep JSON containment with a nonrecursive inspection of accepted values."""

import json

from unstd.serde import jsonx


def json_nesting(size):
    """Match interpreter rejection or count the complete independently known shape."""
    assert 1 <= size <= 20000
    assert jsonx.loads('{"control":[1,true,null]}') == {"control": [1, True, None]}
    wire = "[" * size + "0" + "]" * size
    try:
        expected = json.loads(wire)
    except (RecursionError, ValueError) as error:
        expected_error = type(error)
    else:
        expected_error = None
        del expected
    try:
        value = jsonx.loads(wire)
    except (RecursionError, ValueError) as error:
        actual_error = type(error)
    else:
        actual_error = None
    if actual_error is not None:
        assert expected_error is not None
        assert actual_error is expected_error
        return {"depth": size, "outcome": actual_error.__name__}
    assert expected_error is None
    depth = 0
    while isinstance(value, list):
        assert len(value) == 1
        depth += 1
        value = value[0]
    assert depth == size
    assert type(value) is int
    assert value == 0
    return {"depth": depth, "outcome": "value"}
