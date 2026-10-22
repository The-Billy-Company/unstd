Clarify that utility extensions have their own contracts rather than promising
an import-only stdlib migration. Correct Decimal hook documentation: native
Decimal encoding bypasses `float_enc_hook`; `decimal_format` controls its wire
type. Tests pin both string and numeric Decimal output with a hook installed.
