---
doc_radar:
  sentinels:
    - file: src/unstd/time/timeutil.py
      contains: ['strftime = _time.strftime', 'gmtime = _time.gmtime', 'localtime = _time.localtime', 'strptime = datetime.strptime', 'combine = datetime.combine']
---

We expose native time tuples, explicit format parsing and date/time assembly through direct stdlib bindings. Formatting keeps the platform directives and local-time default; parsing and assembly keep their native timezone, fold and error behavior without an extra call frame.
