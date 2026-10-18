We added `unstd.compress.gzipx` behind `unstd[compress]`: gzip file and one-shot
operations accelerated with zlib-ng, with exact stdlib bindings on a base install.
Streams retain stdlib framing, ownership, modes, seeking and error families;
one-shot defaults follow the running interpreter. Compressed bytes may differ
between codecs while recovered content remains identical.
