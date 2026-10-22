Benchmark calibration now confirms the iteration count with a second batch so
one descheduled cheap call cannot make all later batches too small. Verification
reports retain raw wall and thread-CPU samples for every initial/recheck attempt.
Added alternating independent random-workload controls with primitive/source
identity evidence. Environment sensitivity remains diagnostic: original latency
ceilings and failure verdicts stay intact.
