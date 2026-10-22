Reject explicitly empty TLS CA, certificate and key paths. An empty CA pin can
no longer restore default public trust, and empty identity paths cannot silently
omit mTLS. Use None to omit a path.
