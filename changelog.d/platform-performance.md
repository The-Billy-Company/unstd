Performance regression comparisons now run independently on Linux and macOS
with Python 3.13 and 3.14, including for native-source changes. Python 3.14
exercises its distinct native JSON and clone fast paths. Each platform and
interpreter measures both revisions on the same worker and retains its own
evidence; a failure in one comparison does not cancel the others. These
comparisons detect regressions without promising identical elapsed time across
different hardware and operating systems.
