# Backend

Python package for collection, local querying and analysis. From the repository root:

```sh
python3 -m backend.tado_history --help
python3 -m backend.octopus_api --help
.venv/bin/python -m backend.web_server
```

Prefer `make demo` for an isolated development server. Running the module directly uses normal
runtime defaults; do not run collectors without configuring and authorizing the target installation.

| Module | Owns | External access |
|---|---|---|
| `tado_history` | Day reports, SQLite, retention guards, quota | Tado, read operations and auth refresh |
| `environment_history` | Hue observations and weather history | Paired Hue bridge and Open-Meteo |
| `octopus_api` | Explicit invoice/account read commands | Octopus Spain and signed document downloads |
| `web_server` | Local read API and source validation | Delegates refresh; does not expose credentials |
| `refresh_coordinator` | Staleness, single flight and bounded processes | Fixed installed collector subprocesses |
| `heating_sessions` | Union of demand and thermal observations | None |

Importing a module must not collect data, bind a port or install a service. Entrypoints belong under
`if __name__ == '__main__'`. Keep collector modules standalone-compatible because macOS installers
copy them individually. The installed web service uses the package and module entrypoint.

Use UTC instants in storage and explicit local calendar boundaries for aggregations. Preserve missing
coverage; a disconnected sensor is not an off state. See [architecture](../docs/architecture.md)
and [tests](../tests/README.md) before changing persistence or session semantics.
