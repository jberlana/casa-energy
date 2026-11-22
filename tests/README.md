# Tests

Run `make test` from the repository root, or a focused suite:

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_sessions.py' -v
```

All fixtures are synthetic; tests use temporary directories and mocked remote access. Import runtime
code through `backend`. Do not require a signed-in account, paired bridge, real invoice or live server.

The suites cover collection integrity, quota, storage, read-only API behavior, interval union,
DST, unknown coverage, refresh concurrency, invoice privacy and deployment paths.
Keep fixtures next to their tests until several suites genuinely share one. Never derive fixtures
from a private database, even after deleting obvious account fields.

Unit tests do not establish browser gesture correctness or real-provider behavior. Record those
checks separately when required by the change. Test names should describe the behavior being protected.

`test_windows.py` covers ventilation evidence, durable/correctable feedback, experimental holdouts,
source isolation, read-only history, and bounded calibration across restarts and stale observations.
