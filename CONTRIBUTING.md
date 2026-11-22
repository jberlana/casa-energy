# Contributing

## Development environment

Use Python 3.11+ and Node.js 22+. Start at the repository root:

```sh
make setup
make demo
```

The demo uses `.local/` for runtime state and starts without private configuration. It listens at
`http://127.0.0.1:8765`; set `TADO_WEB_PORT` to use another port. For frontend hot reload, run
`make dev-web` in a second terminal. Vite proxies API calls to port 8765; update its local proxy
when using another backend port. Do not commit a personal hostname.

## Change workflow

1. Read the directory README and `docs/architecture.md`; choose the module that owns the behavior.
2. Create a focused branch, such as `fix/session-boundaries` or `feat/new-provider`.
3. Implement using synthetic fixtures. Add tests for behavior that could regress, rather than tests
   that duplicate implementation details. Update docs for commands, contracts and configuration.
4. Run `make check`. For UI/gesture changes, verify mobile and desktop behavior in a browser.
5. Review new files individually and update `PUBLIC_FILES.txt`. Review `git diff`, stage only the
   intended paths, then run `make check-publication` and `git diff --cached --check`.
6. Commit and open a pull request with problem, resulting behavior, validation and relevant limits.
   Publication to the public repository must stay within the authorized task scope.

`make check-publication` reads the Git index. Stage both the manifest and changed files before
running it; passing the check does not validate unstaged edits. It is an additional guard, not proof
that arbitrary data is anonymous. Never include real household data in issues, PRs or test artifacts.

## Structure and boundaries

| Change | Location |
|---|---|
| Tado collection, storage and quotas | `backend/tado_history.py` |
| Hue / weather collection | `backend/environment_history.py` |
| Local API and request validation | `backend/web_server.py` |
| Refresh scheduling and concurrency | `backend/refresh_coordinator.py` |
| Session calculations | `backend/heating_sessions.py` |
| Octopus read client | `backend/octopus_api.py` |
| Heating charts and session inspector | `web/src/features/heating/` |
| Weather UI | `web/src/features/environment/` |
| Shared browser contracts and refresh | `web/src/lib/` |
| Service installation | `scripts/macos/` |

Keep Python imports package-relative inside `backend`; tests use `from backend import ...`.
Use `python -m scripts.macos.<module>` for installers. Do not patch `sys.path` to make scripts work.
The collector files copied to installed services remain standalone; see `scripts/README.md`.

## Data and compatibility

A new provider should have an isolated collector, explicit freshness and failure behavior, bounded
requests, persistent backoff, and a documented local data contract. Keep authorization server-side.
Database/schema changes must preserve existing records and include migration and recovery guidance.
Do not allocate gas consumption to a session without an explicit model and source provenance.

## Dependencies

`web/package-lock.json` fixes the JavaScript dependency tree. Python direct requirements live in
`requirements-web.txt`, with the tested resolved set in `requirements-web.lock`. Change both relevant
files deliberately when updating dependencies; do not regenerate them during unrelated changes.
Highcharts has separate licensing terms. The repository currently has no project software license.

## Commit and PR style

Use `feat`, `fix`, `refactor`, `spike`, `perf`, `style`, `docs`, `test`, or `chore` in lowercase.
Descriptions are short English statements of intent, in present tense and without a final period.
An optional scope identifies a domain: `fix(collector): preserve terminal observations`.

PR descriptions explain the concrete problem and final behavior, followed by actual validation.
Keep build, unit tests, browser checks, real-provider checks and deployed behavior distinct.
