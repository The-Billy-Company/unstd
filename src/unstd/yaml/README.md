# `unstd.yaml`

Safe YAML 1.2 loading and document editing behind `unstd[yaml]`. We use
[ruamel.yaml](https://yaml.dev/doc/ruamel.yaml/overview/) for native values and
comment-preserving edits. The base install still has no third-party dependencies;
YAML calls without the extra raise an `ImportError` naming the install command.

```python
from unstd import yaml

value = yaml.loads("enabled: true\nname: on\n")
text = yaml.dumps({"enabled": True})

doc = yaml.parse("# service\nport: 8000  # public\n")
if isinstance(doc, dict):
    doc["port"] = 9000
text = yaml.dumps(doc)  # keeps the comments

with open("manifests.yaml", "rb") as fp:
    for document in yaml.load_all(fp):
        ...
```

`load` accepts text or binary handles; `loads` and `parse` accept text or bytes.
`dump(value, fp)` and `dump_all(values, fp)` write text. Streams belong to the
caller and remain open. `load_all` is lazy; keep its source open during iteration.
Single-document APIs reject a second document, and empty input returns `None`.

`YamlValue` describes scalar, mapping, sequence, tuple and set values. Mapping keys
are hashable YAML values, and timestamps retain their native `date`/`datetime`
types. Narrow a document's shape before accessing it; decoding YAML does not
validate an application schema. Aliases remain shared references. The native
reader supports cycles; the editable reader rejects unresolved recursive aliases
that its backend would otherwise silently replace with null.

Both readers reject duplicate keys and unknown tags, including Python object tags.
Duplicate explicit keys remain errors in mappings containing merges; a single
explicit key may override an inherited value, preserving normal merge precedence.
The editable reader retains comments, quote style, key order, anchors and merges;
the emitter can normalize indentation. We create an isolated parser for each
operation and use `pure=True`: ruamel's optional C loader has
[different scalar semantics](https://yaml.dev/doc/ruamel.yaml/basicuse/).
Documents without a version directive default to YAML 1.2; an explicit YAML 1.1
directive retains its declared resolution rules. Safe loading prevents arbitrary
object construction; it does not impose input size or resource limits. Repeated
merges of one anchor are not copied again, so merge cost follows the source size.

Parser errors retain ruamel's source marks and inherit `yaml.YAMLError`. Native
scalar conversion errors propagate from the backend. Arbitrary Python objects
passed to the writer raise a representer error. We do not add interpolation,
environment merging, or implicit schema coercion.

## Implementation layout

Parser safety policy and value validation live in [`_internal/`](_internal/README.md). Public operations keep per-call parser ownership and the existing engine injection point.
