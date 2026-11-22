# Setup and operation

## Connect your own installation

Initial account authorization and bridge pairing are not automated. Each user must obtain their
own authorizations and keep them outside the repository. This is not an official integration.

- **Tado X:** configure `home_id`, `client_id` and `auth_path` in the collector's private
  `config.json`. This code can renew an existing authorization; it does not include the OAuth login flow.
  Default configuration contains no home or client identifier.
- **Hue:** the private `~/.config/tado-experiment/hue-auth.json` file needs `ip`, `host`,
  `username`, `certificate_pem`, `temperature_resource_id` and `connectivity_resource_id`.
  The paired bridge's certificate is validated.
- **Weather:** configure `latitude` and `longitude` in the private `config.json` in the
  `TadoEnvironment` directory. No coordinates are preconfigured. The prototype uses the
  `Europe/Madrid` time zone; adapt it consistently when using another zone.
- **Octopus Spain:** experimental client for queries and document downloads. The session is
  stored outside the project. Legacy password login may become unavailable;
  check the methods supported by the provider before use. Do not generate keys or modify
  contracts through this client. The structured consumption model requires validation against
  the original documents; it is not automatically considered measured consumption.

The minimum configuration is documented in [examples/collector-config.example.json](../examples/collector-config.example.json).
Replace empty values only in a local file ignored by Git.

## Services and limits

Installation modules under `scripts/macos/` create user LaunchAgents.
Review them and configure data sources before running them.
`python3 -m scripts.macos.prepare_wake_install` prepares a separate wake installer that requires administrator access;
it does not run it automatically. Review the example schedules for each installation.

The Mac must be powered on with a user signed in. Locking the screen does not stop services;
sleep prevents the web interface from being served. Wake through Tailscale is not guaranteed.

The Tado collector limits requests and storage and preserves history when errors occur.
Web refresh has an additional maximum of four daily attempts, a cooldown,
a quota reserve for scheduled collection and mutual exclusion between tabs. It never sends
commands to change temperatures. Collectors run as processes separate from the web API.
See [scripts/README.md](../scripts/README.md) for installation commands.

## Refresh from the menu

The three-dot menu in the header refreshes Tado, Hue or Octopus separately in
the local installation. Results appear inside the menu;
on completion, local data is queried again without changing the zoom or selected room.

- **Tado:** queries even when data is recent, while preserving the 30-minute cooldown,
  the shared maximum of four daily web attempts and the reserve for scheduled collection.
  Provider limits and mutual exclusion with calibration still apply.
- **Hue:** queries only the paired sensor, without requesting weather data. It has a one-minute
  cooldown and shares the environment collector lock. A disconnected reading is not a success.
- **Octopus:** uses the existing private session in `~/.config/octopus-experiment` (`OCTOPUS_ROOT`
  allows another private location). It discovers a single account and an unambiguous gas ledger.
  If there are several, set `account` and `ledger` in that directory's private `config.json`, never in Git.
  Expired sessions are renewed only if the provider allows it; there is no web login.

Octopus queries one page of up to 20 invoices and downloads up to three pending PDFs per action.
The private catalog indicates whether more history or documents remain. Another refresh continues
the documents on that page without duplicating them. This is not a complete history import.
A minimal catalog and documents are kept in `catalog.json` and `documents/` within the private
directory. Signed links are not stored. Each run limits GraphQL queries, including authentication,
to 12, each PDF to 8 MiB and total storage to 64 MiB, without deleting files when full.
The cooldown between Octopus actions is 15 minutes.

**Downloading an invoice does not verify its amounts.** The verified invoices used by the chart
remain intact; the menu indicates that documents are awaiting verification. Contracts, meter
readings, payments and heating targets are not modified.

All actions share a single job with automatic refresh. The manual process has a maximum duration
of 190 seconds; failures and cooldowns are shown without exposing private provider errors.
After updating an installation's code, reinstall the web service to copy the new modules as well;
the existing collectors do not need to be reinstalled.

## Local branding

The web installer can read a `branding.json` file with a `name` field and a 180 × 180
`apple-touch-icon.png` from the installed `TadoWeb` application support directory. These
files belong to the local installation. Reinstall the web service to apply changes to the
header, browser tab and Home Screen metadata.

## Last refresh and pull to refresh

The menu shows each provider's last successful refresh, including scheduled collectors. Failed
attempts and quota pauses do not overwrite that time. Missing evidence is shown explicitly.
Tado uses the collector's successful completion, Hue the latest valid local observation, and
Octopus the last completed document synchronization. These are refresh times, not invoice dates
or the time a sensor value last changed.

At the top of the page, pull down on the header or room heading and release after the
refresh hint changes. Chart touches, controls, horizontal movement, pinches and cancelled gestures
are excluded. The menu also offers Refresh all for keyboard and desktop use. One server request reserves all eligible providers under the shared lock, with
independent quotas and results; a skipped or failed provider does not stop the others. The hint
reports partial completion, and the menu gives the individual reasons. Graphs keep their viewport.

## Adaptive Tado request budget

The collector, web refresh and calibration share `tado_quota`. A recent valid
`ratelimit-policy` (`q` and `w`) sets the allowance with a safety reserve of at least
20% or `server_quota_reserve`, whichever is greater. No valid policy means a conservative
100-request reference and a maximum usable budget of 80 (or a lower `daily_request_limit`).
Set `adaptive_quota: false` in private configuration to retain that fixed local cap even
when the server announces a larger allowance. The normal default is adaptive.

Every outbound attempt, including authentication and retries, enters a bounded rolling 24-hour
ledger in the existing SQLite `state` table. UTC daily counters remain for diagnostics;
midnight does not refill the rolling budget. When upgrading an existing installation, daily
counts are placed at the latest possible time within their day, conservatively preserving
past usage. No climate schema or stored measurements change. The ledger retains at most one
bucket per minute. Updated server remaining counts are reduced by subsequent local attempts,
including requests that produce no quota headers. Other clients may still consume the account
allowance between observations; the next response reconciles it.

Manual/automatic web refresh and calibrations reserve only unfinished upcoming collection
slots, plus the latest missed slot if still eligible for retry. If the server provides a reset
deadline, the reserve also covers slots before that deadline (up to the next 24 hours).
Successful/past superseded slots are excluded. Scheduled collection uses the shared budget
without reserving its own work a second time. The four-extra-web-attempts daily cap and
30-minute cooldown remain distinct local guards.

`ratelimit` remaining/reset fields and `Retry-After` (seconds or HTTP date) control pauses.
HTTP 429 without a deadline pauses for 24 hours. After an observation expires, the code falls
back conservatively; it does not assume the remote quota has refilled. The menu distinguishes
server pauses, safety margins, local budgets, upcoming collection reserves, web-attempt limits
and cooldowns. “Check again” is a reevaluation time, not a guaranteed provider reset.

Deploy both the collector and web package together: their quota accounting must agree.
The collector installer copies `tado_quota.py` alongside the standalone script. A rollback
must stop all writers and preserve request counters; before restarting an older/newer mix,
reconcile usage conservatively from those counters. Do not reset counters to regain quota.
