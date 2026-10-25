Add lazy `yaml.pyyaml` and `yaml.ruamel` bindings to the original backend modules. Loader, dumper, parser, event and node objects keep their exact identity and behavior; importing one contract does not load the other backend. Native YAML 1.2 helpers bind their original callables on first access.

We verify both YAML backends in isolated wheel and source-distribution consumers, including their exact dependency closure and module origins.
