# Data and publication

Household history stays in each installation. This repository publishes only code,
empty configuration and synthetic tests. No example files come from real accounts.

Do not add SQLite databases, exports, reports, invoices, PDFs, screenshots, traces,
logs, sessions, passwords, private certificates, device identifiers, private remote-access
domains, home coordinates or documents describing a real installation.
Runtime paths and authorization files are resolved locally and are not versioned.

The demo uses rooms `Demo A` through `Demo D`, with IDs 101–104 independent of any
installation. Readings are calculated from formulas, not extracted from private databases.

`PUBLIC_FILES.txt` is an explicit list of reviewed files. The publication check rejects
tracked files outside that list and common patterns for credentials or private locations.
It does not replace human review: a real reading or an identifier in an unfamiliar format can
look like ordinary data. Never automatically copy installation files into the repository.

The check also reviews authors, committers and messages in branch history, along with the
identity of the next commit. Use an alias and a GitHub `noreply` address, and enable email
privacy for operations performed through GitHub. CI adds a secret scan of branch history.
A passing result does not prove that archived PR objects or cached views have been removed.

For later changes, review `git diff --cached`, run tests and the publication check.
The initial version was prepared in a separate directory, without importing the private
installation's Git history or runtime files.
