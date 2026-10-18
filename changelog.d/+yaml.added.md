We added `unstd.yaml` behind `unstd[yaml]`: safe YAML 1.2 native-value loading,
multi-document streams, and comment-preserving parse/edit/write. Duplicate keys
and unknown tags are rejected; parser instances and scalar semantics are isolated
from optional C extensions. The base install keeps zero third-party dependencies.
