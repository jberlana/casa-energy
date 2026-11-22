# Ventilation detection and guided calibration

The timeline shows small ventilation markers, not a new dashboard section. Touch/click a marker
to inspect evidence. Optional labels appear only in that detail, until three distinct events have
been reviewed in a room. Guided openings count towards that limit. `unknown` counts as an answered
question but never trains sensitivity. Corrections remain available; no notifications are sent.

## Evidence and detector

`window_detection` is pure and versioned. It uses original historical samples, not chart-decimated
points. The initial candidate is a sustained fall of 0.7 °C within 30 minutes, with continuous known
connectivity and no sampling gaps over 20 minutes. A subsequent sample must support the fall;
single down/up spikes and humidity-only changes do not create candidates.

Explanations include target changes, the end of demand, simultaneous cooling in other rooms, cached
weather, and absolute humidity calculated from relative humidity and temperature. An entrance sensor
is not assumed to measure outside temperature. A 30-day reference compares cooling rates in the
same demand state only after 30 comparable intervals exist. Missing reference is explicit.

Two confirmed positive events on distinct UTC days adjust sensitivity to 70% of their median
historical drop, clamped to 0.35–1.2 °C. This conservative experimental rule is not a probability or a
validated classifier. Later guided trials are holdouts. Negative labels stay visible as corrections;
they do not silently raise a threshold. Confounded experiments do not train. Missing observations
must never be interpreted as a closed window or heating off.

The provider `openWindow` object with a boolean `activated` field is separately labelled as a Tado
signal; unrecognized shapes are not guessed. `activated` is a shut-off
flag, not a physical contact sensor. Null values, expiry and the end of cooling do not establish
window closure. Non-null payload semantics remain provisional until a real positive response is
observed. The read endpoint does not fetch provider data.

## Private persistence and API

- `GET /api/window-events?source=real|demo&room=…&start=…&end=…` returns events, onset uncertainty,
  evidence, labels, detector version, sensitivity and whether to ask for feedback. Times are UTC
  milliseconds. Day-aligned calculation margins preserve identity during pan/zoom.
- `POST /api/window-feedback` takes `source`, `room`, `eventId`, `start`, `end`, `label` (`yes`, `no`,
  `unknown`). It requires matching Origin/Host and `X-Casa-Feedback: 1`. The server revalidates the
  event rather than trusting client evidence. Demo and real labels are isolated.
- `window-insights.sqlite3` under `TADO_WEB_ROOT` holds labels, sessions and minimal observations.
  It is created only on explicit writes, mode 0600, with a 32 MiB SQLite page cap. Exhaustion stops
  writes; it never deletes confirmations. Events are calculated on demand and do not accumulate.

There is no migration of `history.sqlite3`. The additive insights store is schema version 1 and can
be backed up independently using SQLite's backup API while writers are idle. Restore the whole
insights database to recover labels; reinstalling code does not erase it. To roll back the feature,
restore the prior application build and leave both databases in place. Unknown store versions fail
closed. Avoid copying household files into the checkout or publication fixtures.

## Guided session (operator CLI, no permanent UI)

Use the project virtual environment from the repository root. A paired/configured collector and a
recent known server quota are required. Run only with the participant present. Example commands use
a synthetic room ID; obtain the real ID locally without adding it to project files.

```sh
.venv/bin/python -m backend.window_calibration start --room 1
.venv/bin/python -m backend.window_calibration run --session SESSION_ID
# In another terminal, only after canOpen becomes true and the participant confirms:
.venv/bin/python -m backend.window_calibration mark --session SESSION_ID opened
.venv/bin/python -m backend.window_calibration mark --session SESSION_ID closed
.venv/bin/python -m backend.window_calibration status --session SESSION_ID
```

`start` allocates a bounded session without network access. `run` polls room states every two minutes
and prints operator instructions. `tick` performs at most one due poll for orchestration without a
long-running command. There is no installed daemon or automatic restart. Both paths obey the same
persisted budget, cadence and expiry. Start only one session at a time.

Keep the window closed for ten minutes. The system requires two fresh observations and recent data
before `canOpen`. A new sensor timestamp or a changed temperature without a timestamp is evidence
of freshness; an unchanged cached response is not. Lack of freshness terminates the baseline by
14 minutes. Do not ask the participant to open a window unless `canOpen` is true.

Ask the participant to open normally and confirm the actual action, then to close after roughly
five minutes (earlier is always fine). Confirm closure and observe recovery until the session's
30-minute deadline. The assistant must remain in the active conversation to give instructions;
the CLI cannot send messages or close the window. If interrupted, remind the participant to close
the window. Use `mark … stop` to stop, or `mark … confounded` to record an opened door or other
change during the trial. Never turn on heating solely to create a trial.

Every HTTP attempt, retry and token renewal counts towards 20 requests. Each attempt shares the
collector lock, daily quota, backoff and scheduled-collection reserve. A scheduled collector takes
priority. Unknown/stale quota, deadline, exhausted budget or errors stop calibration rather than
bypassing guards. Failure/expiry cannot be resumed; an explicit new session is required. Normal
collection cadence and existing climate data are not modified.

## Evaluation

After regular collection has covered the trial and its recovery period:

```sh
.venv/bin/python -m backend.window_calibration report --room 1
```

The report compares fresh experimental observations with original historical resolution, labels
training versus holdout trials, and reports historical coverage and baseline candidates. An opening
visible in two-minute observations may be missed by fifteen-minute history. Incomplete cases remain
unscored. Confirmed opening/closing times persist even when a trial cannot train the detector.

Holdout sensitivity is a count of detected versus observed openings; precision remains unknown
without independently reviewed natural candidates. Three answered questions end UI prompts, not
scientific uncertainty. No burner runtime, gas loss or money saved is inferred from a window event.

## Validation

`tests/test_windows.py` uses synthetic data and mocked API calls. Cover gaps, disconnections, spikes,
ordinary cooling, changed demand, annotation persistence/privacy, cross-origin rejection, independent
demo labels, quota accounting, cadence, stale observations and expiry across restarts. Browser checks
must additionally verify marker focus, mobile details, prompt cessation, and unchanged pan/zoom.
No unit or browser test establishes live Tado freshness or household detection accuracy.
