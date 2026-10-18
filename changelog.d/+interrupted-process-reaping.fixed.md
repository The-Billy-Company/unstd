Kill and reap proc.run's direct child before propagating cancellation, including
the KeyboardInterrupt path where subprocess.run could return with an unreaped
child. Preserve captured output, timeout behavior and callback semantics; cover
run, capture and run_ok with real SIGINT regression tests and recovery.
