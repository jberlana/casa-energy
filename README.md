# Casa Energy

A local prototype for exploring heating history: temperature, targets, combined demand,
outdoor conditions and gas invoices. React, TypeScript and Highcharts Stock over a FastAPI and SQLite backend.

**This repository contains code and synthetic tests. It does not include real readings, invoices,
credentials, installation identifiers or private addresses. The interface starts in demo mode.**

## Features

- A continuous timeline with panning, pinch to zoom and reading inspection.
- Sessions calculated from the union of room demand, without double-counting overlaps.
- Optional weather context from Open-Meteo and a local Hue sensor.
- Locally imported verified invoices, separating consumption from fixed charges.
- Silent refresh of stale data while the web interface is visible, with shared limits and locking.
- A menu to refresh Tado, Hue and Octopus documents on demand, with shared status and limits.
- Optional private access through Tailscale; the server listens on loopback.
- Possible ventilation events on the chart, with discreet feedback and [guided calibration](docs/ventilation.md).

Demand does not confirm burner operation or measure gas consumption. Demo costs are hypothetical examples,
not estimates calibrated for a home. Gaps and incomplete periods are preserved.

## Quick start

Python 3.11+ and Node.js 22+. From the repository root:

```sh
make setup
make demo
```

Open `http://127.0.0.1:8765`. The demo needs no accounts and uses `.local/` for its state.
If the port is busy, use `TADO_WEB_PORT=8767 make demo`. The real-history health endpoint requires
a configured database; the demo works without one.

## Organization

| Directory | Responsibility |
|---|---|
| [backend/](backend/README.md) | Collectors, local API and calculations |
| [web/](web/README.md) | Interface organized by feature |
| [scripts/](scripts/README.md) | Publication and macOS installation |
| [tests/](tests/README.md) | Tests using synthetic data |
| [examples/](examples/README.md) | Empty configuration examples |
| [docs/](docs/README.md) | Architecture, operation and privacy |

## Development

Read [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) and the
[architecture](docs/architecture.md). `make check` runs tests and the build; after reviewing and
staging files in Git, `make check-publication` checks the index against
`PUBLIC_FILES.txt`. Only explicitly reviewed files are published.

Provider configuration and operating limits are documented in [setup](docs/setup.md).
Installation data must never be copied into the repository. Code changes do not automatically
update services that are already installed.

All maintained code, comments, documentation, examples and application text must be in English.
Provider contracts and user-supplied data retain their original values.

## Dependencies and scope

Highcharts retains its credits and has its own license terms. Check the
[Highcharts license](https://www.highcharts.com/license) before distributing or commercially using
this prototype. Publishing this code does not grant a license to its commercial dependencies.

The project does not currently include a software license. Provider APIs may change;
future compatibility or availability is not guaranteed.
