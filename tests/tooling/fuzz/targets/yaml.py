"""Safe YAML raw parsing, independent values, tag refusal and alias identity.

Envelope: byte 0 modulo 4 selects raw bytes, a known JSON-subset YAML model,
an unknown/unsafe explicit tag, or an acyclic alias graph. Remaining bytes are
raw source or model entropy. Alias depth is 1..24 and fanout 2..8; output graphs
are traversed by identity, never recursively expanded. This target requires the
yaml extra and is installed-backend only.

Raw safe-loader values/errors compare with a separately configured pure ruamel
safe parser, preserving exact backend error types/messages. When that parser
fails to identify merged duplicate keys, an independent round-trip parser
identifies the explicit duplicate and requires rejection. A safe constructor
error reached before that duplicate retains its own exact diagnostics.
Generated values
have an independent Python model and exercise safe/editable/stream/multidocument
paths. Explicit unknown and Python tags must fail every reader. These laws test
the wrapper's security policy rather than claiming a separate YAML parser.
"""

from __future__ import annotations

from datetime import date
import io
import json

from ruamel.yaml import YAML
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import YAMLError

from unstd import yaml


def _graph(value):
    """A bounded graph fingerprint that retains alias sharing and cycles."""
    pending = [value]
    identities = {}
    nodes = []
    for node in pending:
        if node is None or isinstance(node, str | int | float | bool | bytes | date):
            nodes.append(("scalar", type(node).__name__, repr(node)))
            continue
        identity = id(node)
        if identity in identities:
            nodes.append(("ref", identities[identity]))
            continue
        identities[identity] = len(identities)
        if isinstance(node, dict):
            nodes.append(("dict", len(node)))
            for key, child in node.items():
                pending.extend((key, child))
        elif isinstance(node, list | tuple):
            nodes.append((type(node).__name__, len(node)))
            pending.extend(node)
        elif isinstance(node, set):
            nodes.append(("set", len(node)))
            pending.extend(
                sorted(node, key=lambda item: (type(item).__name__, repr(item)))
            )
        else:
            msg = f"unexpected safe YAML node {type(node).__name__}"
            raise TypeError(msg)
        assert len(pending) <= 65536
    return nodes


def _outcome(operation, source):
    try:
        return ("value", _graph(operation(source)))
    # Scalar constructors can reject malformed dates/binary with ValueError
    # rather than YAMLError; retain exact type/message parity for those too.
    except (YAMLError, ValueError, RecursionError) as error:
        return (
            "error",
            type(error),
            "" if isinstance(error, RecursionError) else str(error),
        )


def fuzz(data: bytes) -> None:
    """Check real safe YAML policy without materializing exponential aliases."""
    mode = data[0] % 4 if data else 0
    payload = data[1:]
    if mode == 0:
        reference = YAML(typ="safe", pure=True)
        reference.allow_duplicate_keys = False
        expected = _outcome(reference.load, payload)
        duplicate_merge = False
        if expected[0] != "error" or expected[1] is not DuplicateKeyError:
            policy = YAML(typ="rt", pure=True)
            policy.allow_duplicate_keys = False
            try:
                policy.load(payload)
            except DuplicateKeyError:
                duplicate_merge = True
            except AttributeError as error:
                # ruamel's editable constructor leaves a recursive merge
                # referent as None. This auxiliary duplicate-key probe cannot
                # handle that graph; the safe reference and both product
                # readers below still have to agree exactly.
                if error.name != "add_referent" or error.obj is not None:
                    raise
            except (YAMLError, ValueError, RecursionError):
                # Only explicit duplicate errors amend the safe value oracle.
                # Other round-trip limitations do not alter safe parser parity.
                pass
        if duplicate_merge:
            for operation, source in (
                (yaml.loads, payload),
                (yaml.load, io.BytesIO(payload)),
            ):
                result = _outcome(operation, source)
                assert result[0] == "error"
                if result[1] is not DuplicateKeyError:
                    # The round-trip loader can reach a duplicate after the
                    # safe constructor already rejected an earlier key/value.
                    # Require that exact safe error, never arbitrary refusal.
                    safe = YAML(typ="safe", pure=True)
                    safe.allow_duplicate_keys = False
                    safe_source = (
                        io.BytesIO(payload)
                        if isinstance(source, io.BytesIO)
                        else payload
                    )
                    assert result == _outcome(safe.load, safe_source)
                if isinstance(source, io.BytesIO):
                    assert not source.closed
            return
        assert _outcome(yaml.loads, payload) == expected
        source = io.BytesIO(payload)
        reference = YAML(typ="safe", pure=True)
        reference.allow_duplicate_keys = False
        assert _outcome(yaml.load, source) == _outcome(
            reference.load, io.BytesIO(payload)
        )
        assert not source.closed
        return
    if mode == 2:
        selector = payload[0] if payload else 0
        tag = (
            "!unstd_fuzz_" + payload[:16].hex(),
            "!!python/object/apply:builtins.eval",
            "!!python/object/new:builtins.str",
        )[selector % 3]
        source = tag + " [" + json.dumps(payload.hex()) + "]\n"
        for operation in (
            yaml.loads,
            yaml.parse,
            lambda text: yaml.load(io.StringIO(text)),
            lambda text: list(yaml.load_all(text)),
        ):
            try:
                operation(source)
            except YAMLError:
                pass
            else:
                msg = "unsafe or unknown YAML constructor tag was accepted"
                raise AssertionError(msg)
        assert yaml.loads("safe: true\n") == {"safe": True}
        return
    if mode == 3:
        depth = 1 + (payload[0] % 24 if payload else 0)
        fanout = 2 + (payload[1] % 7 if len(payload) > 1 else 0)
        leaf = payload[2:128].hex()
        lines = ["level0: &level0 [" + json.dumps(leaf) + "]"]
        lines.extend(
            f"level{level}: &level{level} ["
            + ", ".join([f"*level{level - 1}"] * fanout)
            + "]"
            for level in range(1, depth + 1)
        )
        source = "\n".join(lines) + "\n"
        for operation in (yaml.loads, yaml.parse):
            value = operation(source)
            assert len(value) == depth + 1
            assert value["level0"] == [leaf]
            for level in range(1, depth + 1):
                children = value[f"level{level}"]
                assert len(children) == fanout
                assert all(child is value[f"level{level - 1}"] for child in children)
        return
    expected = {
        "payload": payload.hex(),
        "enabled": bool(sum(payload) % 2),
        "empty": None,
        "numbers": list(payload[:64]),
        "nested": {"text": 'yes: no\n"\\', "negative": -len(payload)},
    }
    source = (
        "# fuzz-preserved-comment\n" + json.dumps(expected, ensure_ascii=True) + "\n"
    )
    assert yaml.loads(source) == expected
    parsed = yaml.parse(source)
    assert parsed == expected
    encoded = yaml.dumps(parsed)
    assert "# fuzz-preserved-comment" in encoded
    assert yaml.loads(encoded) == expected
    stream = io.StringIO()
    yaml.dump(expected, stream)
    assert not stream.closed
    assert yaml.loads(stream.getvalue()) == expected
    assert list(yaml.load_all("---\n" + source + "---\n" + source)) == [
        expected,
        expected,
    ]
