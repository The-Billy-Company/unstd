`ensure_dir(..., check_first=False)` retains `Path.mkdir`'s creation attempts
and audit events. The default keeps existing directories as audit-free no-ops.
