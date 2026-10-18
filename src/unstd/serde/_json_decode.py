"""Conservative proofs for the optional JSON decoder's exact-value path.

orjson's integer decoder constructs Python integers from exact signed/unsigned
64-bit values; floating tokens and integers outside that range become Python
floats or raise. Rejecting *every* surviving float therefore also rejects its
silent integer rounding. A failed proof must use the original input with
stdlib; it must never try to repair the backend's already rounded numbers.

The backend rejects non-finite literals, invalid UTF-8, unpaired surrogates,
UTF-16/32 inputs and invalid syntax. The caller retries these with stdlib so
both accepted extensions and precise diagnostics remain stdlib-owned.

Sources: https://github.com/ijl/orjson#deserialize and
https://github.com/ijl/orjson/blob/master/src/deserialize/backend/yyjson.rs.
"""

from __future__ import annotations

from typing import Any


_EXACT_SCALARS = {str, int, bool, type(None)}
_MAX_OPENERS = 64
_COUNT_WINDOW = 4096


def _verify_result(value: Any) -> bool:
    """Keep the stdlib decoder's two Python validation frames before returning.

    JSONDecoder.decode calls raw_decode. A single flat validation frame could
    otherwise accept input when the caller has insufficient Python recursion
    headroom for stdlib. RecursionError from this probe is retried by stdlib.
    """
    return _decoded_exact(value)


def _decode_eligible(document: Any) -> bool:
    """Bound nesting without inspecting subclasses or parsing string contents.

    Counting raw openers deliberately rejects some harmless string contents
    and wide documents. It bounds all source containers, including duplicate
    object values discarded during decoding. ``find`` quickly handles flat
    documents, then native counting batches repeated openers in a bounded
    window. Beyond that window, repeated ``find`` stops at the source bound.
    """
    kind = type(document)
    openers: tuple[Any, Any]
    if kind is str:
        openers = ("[", "{")
    elif kind is bytes:
        openers = (b"[", b"{")
    else:
        return False
    # Many small nested records are already cheap in CPython's C scanner;
    # checking all their result containers can cost more than parsing. Retain
    # the wider bound only when input size can amortize that proof.
    limit = 8 if len(document) < 4096 else _MAX_OPENERS
    count = 0
    for opener in openers:
        position = document.find(opener)
        if position < 0:
            continue
        count += 1
        if count > limit:
            return False
        position = document.find(opener, position + 1)
        if position < 0:
            continue
        end = position + _COUNT_WINDOW
        count += document.count(opener, position, end)
        if count > limit:
            return False
        position = document.find(opener, end)
        while position >= 0:
            count += 1
            if count > limit:
                return False
            position = document.find(opener, position + 1)
    return True


def _decoded_exact(value: Any) -> bool:
    """Accept only exact builtin trees without any floating-point values.

    Iteration avoids adding a separate Python recursion limit. The bulk type
    check runs in C for the common flat-container case. Checking the *result*
    avoids rescanning string contents in Python or a regular expression.
    """
    pending = [value]
    while pending:
        item = pending.pop()
        kind = type(item)
        if kind in _EXACT_SCALARS:
            continue
        if kind is list:
            values = item
        elif kind is dict:
            values = item.values()
        else:
            return False
        kinds = set(map(type, values))
        if float in kinds:
            return False
        if not kinds <= _EXACT_SCALARS:
            pending.extend(values)
    return True
