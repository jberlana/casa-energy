# Working in Casa Energy

## Read first

Read `README.md`, `CONTRIBUTING.md`, and the README in the directory being changed.
Use `docs/architecture.md` for data ownership and dependency boundaries. The public repository
is the development source; private installations and their data are separate runtime environments.

## Scope and privacy

- Work directly on `main` by default. Create a branch or pull request only when the user requests it.
- Keep changes focused on the requested outcome. Preserve unrelated local edits.
- Use synthetic data and temporary directories for development and tests. Never import real
  readings, invoices, screenshots, logs, credentials, personal paths, device IDs or coordinates.
- Do not load a private installation to manufacture a fixture. Public configuration examples
  must be empty or clearly fictitious. Keep the demo independent of real providers.
- No network calls to a user's devices/accounts or installation of LaunchAgents unless the task
  authorizes that work. Ordinary checks must not affect running services.
- `PUBLIC_FILES.txt` is an explicit reviewed list, not a generated inventory of the whole checkout.
  Add or rename entries deliberately. Never publish files just because `.gitignore` allows them.

## Architecture

- `backend/` contains the Python runtime. Invoke modules from the repository root with `python -m`.
- Collectors own remote access and writes; API read endpoints query local storage. The refresh
  coordinator may start only fixed, bounded collectors. Keep quotas, locking and backoff intact.
- `heating_sessions.py` contains pure calculations. No network or storage access there.
- `web/src/features/` owns domain UI; `web/src/lib/` holds genuinely shared contracts/utilities.
  Keep expensive gestures independent of React fetch/render cycles.
- `scripts/macos/` owns macOS installation, not application logic. Keep provider collectors
  executable as standalone installed scripts; the web service is installed as a Python package.
- Avoid abstract frameworks for hypothetical integrations. Extract a shared interface once a
  second concrete implementation needs it, and document the actual contract.

## Correctness

- Demand is not burner runtime or measured gas. Never label synthetic or inferred data as real.
- Preserve unknown intervals, sample provenance, DST behavior, and incomplete session boundaries.
- Do not weaken incomplete-report rejection without evidence and a regression test that proves
  previous data survives. Schema changes need an explicit migration and recovery plan.
- Preserve pan, pinch, selected room and historical viewport across data refreshes.
- Update related documentation and installer paths when moving files.

## Validation and delivery

Run `make check` after runtime/import changes. Use focused tests while developing. For visual or
gesture changes, also verify the browser and distinguish emulation from physical-device evidence.
After reviewing and staging the intended files, run `make check-publication` and inspect
`git diff --cached`. The checker examines the staged tree, not unstaged edits.

Report what changed, validation performed and remaining limits. Do not claim a build proves
browser behavior, a live integration, or deployment. Do not publish unreviewed generated artifacts.

## Commits

Use English, present tense, lowercase conventional prefixes, short intent-focused descriptions,
and no final period: `<type>(<optional-scope>): <description>`.
Allowed types: `feat`, `fix`, `refactor`, `spike`, `perf`, `style`, `docs`, `test`, `chore`.
Keep unrelated changes in separate commits. Scope is recommended when it helps identify ownership.
