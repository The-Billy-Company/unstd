Reject duplicate explicit YAML keys even when their mapping uses a merge.
Safe loading now checks source keys before merge flattening; legitimate single
overrides, merge precedence and reused anchors remain supported.
