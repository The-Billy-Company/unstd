---
doc_radar:
  sentinels:
    - file: src/unstd/iters/consecutive.py
      contains: ['yield from batched(iterable, n)', 'previous, view = tee(view)', 'window.extend(islice(it, step - 1))']
    - file: src/unstd/iters/distinct.py
      contains: ['candidate = key(element)']
---

We prepare wide sliding windows in linear time, move strided window advances
into `deque.extend`, and reduce Python overhead in non-strict chunks and keyed
deduplication. The iterator API, lazy reads, mixed-key equality, and bounded
retention stay the same; no extra or native backend is required.
