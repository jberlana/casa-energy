"""Local history API. Climate reads are read-only; annotations use a separate private store."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import io
import json
import math
import os
from pathlib import Path
import sqlite3
from .environment_history import read_environment
from .heating_sessions import analyse
from .refresh_coordinator import Coordinator
from .window_detection import Context, detection, guided_annotations
from .window_store import WindowStore
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT = Path(os.environ.get("TADO_DATA_ROOT", Path.home() / "Library/Application Support/TadoHistory"))
DIST = Path(os.environ.get("TADO_WEB_DIST", Path(__file__).resolve().parents[1] / "web/dist"))
INVOICES = Path(os.environ.get("CASA_INVOICES", Path(__file__).resolve().parents[1] / "output/octopus/verified-invoices.json"))
DAY = 86_400_000
MINUTE = 60_000
DEMO_END = int(datetime.now(timezone.utc).timestamp() // 60) * MINUTE
DEMO_START = DEMO_END - 180 * DAY
ROOM_NAMES = {1: "Salón", 3: "Dormitorio", 2: "Habitación 2", 5: "Despacho"}
HEAT_LEVELS = {"LOW", "MEDIUM", "HIGH", "HEATING"}
refresh_coordinator = Coordinator()
window_store = WindowStore(Path(os.environ.get('TADO_WEB_ROOT', Path.home() / 'Library/Application Support/TadoWeb')))
app = FastAPI(title="Casa · historial local", docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
    "127.0.0.1", "localhost", "testserver",
    *os.environ.get("TADO_ALLOWED_HOSTS", "").split(","),
])


@app.middleware("http")
async def headers(request, call_next):
    result = await call_next(request)
    result.headers["X-Content-Type-Options"] = "nosniff"
    result.headers["Referrer-Policy"] = "no-referrer"
    result.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'"
    )
    result.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
    return result


def ms(stamp):
    return int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)


def iso(stamp):
    return datetime.fromtimestamp(stamp / 1000, timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def database():
    try:
        db = sqlite3.connect(f"file:{ROOT / 'history.sqlite3'}?mode=ro", uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")  # Consistent view across samples and intervals during a collector commit.
        yield db
    except sqlite3.Error as exc:
        raise HTTPException(503, "El histórico no está disponible. Inténtalo de nuevo.") from exc
    finally:
        if 'db' in locals():
            db.close()


def validate_source(source):
    if source not in ("real", "demo"):
        raise HTTPException(422, "Origen desconocido")


def window(start, end):
    if not (0 <= start < end <= 4_102_444_800_000) or end - start > 366 * DAY:
        raise HTTPException(422, "El intervalo debe estar entre un minuto y 366 días")


@app.get("/api/health")
def health():
    with database() as db:
        db.execute("SELECT 1 FROM rooms LIMIT 1").fetchone()
    return {"ok": True, "readOnly": True}


@app.get("/api/refresh")
def refresh_status():
    return refresh_coordinator.status()


@app.post("/api/refresh")
def refresh_if_stale(request: Request):
    # Same-origin, explicit browser request. Ordinary GETs remain read-only.
    origin = urlsplit(request.headers.get("origin", ""))
    if (request.headers.get("x-casa-refresh") != "1" or origin.scheme not in ("http", "https")
            or origin.netloc != request.headers.get("host")
            or request.headers.get("sec-fetch-site", "same-origin") != "same-origin"):
        raise HTTPException(403, "Origen no permitido")
    return refresh_coordinator.request()


@app.get("/api/environment")
def environment(start: int, end: int, source: str = "real"):
    validate_source(source)
    window(start, end)
    if source == "demo":
        return {"sensor": "Entrada", "outdoor": [], "weather": [], "days": [],
                "hueStatus": None, "weatherStatus": None, "firstOutdoor": None,
                "location": "Local", "timezone": "Europe/Madrid"}
    try:
        return read_environment(start, end)
    except sqlite3.Error:
        raise HTTPException(503, "El contexto exterior no está disponible") from None


@app.get("/api/meta")
def metadata(source: str = "real"):
    validate_source(source)
    if source == "demo":
        return {"source": source, "start": DEMO_START, "end": DEMO_END,
                "latest": DEMO_END, "updated": DEMO_END, "sampleMinutes": 1,
                "rooms": [{"id": rid, "name": name, "temperature": demo_sample(DEMO_END, rid)[0][1],
                           "humidity": demo_sample(DEMO_END, rid)[0][2], "observed": DEMO_END}
                          for rid, name in ROOM_NAMES.items()]}
    with database() as db:
        rooms = []
        for room in db.execute("SELECT * FROM rooms WHERE active=1 ORDER BY room_id"):
            last = db.execute("SELECT * FROM samples WHERE home_id=? AND room_id=? ORDER BY timestamp DESC LIMIT 1",
                              (room["home_id"], room["room_id"])).fetchone()
            rooms.append({"id": room["room_id"], "name": room["name"],
                          "temperature": last["temperature_c"] if last else None,
                          "humidity": last["humidity_pct"] if last else None,
                          "observed": ms(last["timestamp"]) if last else None})
        times = db.execute("SELECT min(timestamp),max(timestamp) FROM samples").fetchone()
        updated = db.execute("SELECT max(fetched_at) FROM reports").fetchone()[0]
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        return {"source": source, "start": ms(times[0]) if times[0] else now - DAY,
                "latest": ms(times[1]) if times[1] else None, "end": now,
                "updated": ms(updated) if updated else None, "rooms": rooms, "sampleMinutes": 15}


def demo_sample(t, room):
    # Absolute time gives identical overlapping requests. Explicitly synthetic, never persisted.
    hour = (t / 3_600_000 + 1) % 24
    day = math.floor(t / DAY)
    target = 20.5 if 6 <= hour < 9 or 17 <= hour < 23 else 18.0
    temp = 19.5 + .8 * math.sin((hour - 9) * math.pi / 12) + .38 * math.sin(day * .43 + room)
    temp += (room - 2) * .16 + .06 * math.sin(t / MINUTE / 8)
    if day % 5 == 0 and 11 <= hour < 12:
        temp -= 1.9 * math.sin((hour - 11) * math.pi)
    humidity = 48 + 5 * math.sin(hour * math.pi / 12) + room * .8
    return [t, round(temp, 2), round(humidity, 1)], target, "MEDIUM" if target > temp + .2 else "NONE"


def demo_data(room, start, end):
    start, end = max(start, DEMO_START), min(end, DEMO_END)
    samples, events = [], []
    if start >= end:
        return samples, events
    previous = {}
    for t in range(math.ceil(start / MINUTE) * MINUTE, end + 1, MINUTE):
        sample, target, demand = demo_sample(t, room)
        samples.append(sample)
        for series, value in (("settings", {"power": "ON", "temperature": {"celsius": target}}),
                              ("callForHeat", demand), ("connected", True)):
            if series not in previous or previous[series]["value"] != value:
                if series in previous:
                    previous[series]["end"] = t
                previous[series] = {"series": series, "start": t, "end": end, "value": value}
                events.append(previous[series])
    return samples, events


def real_data(db, room, start, end):
    record = db.execute("SELECT home_id FROM rooms WHERE room_id=? AND active=1", (room,)).fetchone()
    if not record:
        raise HTTPException(404, "Habitación desconocida")
    home = record[0]
    samples = [[ms(r[0]), r[1], r[2]] for r in db.execute(
        "SELECT timestamp,temperature_c,humidity_pct FROM samples WHERE home_id=? AND room_id=? "
        "AND timestamp>=? AND timestamp<=? ORDER BY timestamp", (home, room, iso(start), iso(end)))]
    # Day reports can overlap; latest report wins at any interval overlap.
    rows = db.execute("SELECT series,start,end,value_json FROM events WHERE home_id=? AND room_id=? "
                      "AND end>? AND start<? AND series IN ('settings','callForHeat','connected') "
                      "ORDER BY day,start", (home, room, iso(start), iso(end)))
    events = normalize_events([{"series": r[0], "start": ms(r[1]), "end": ms(r[2]),
                                "value": json.loads(r[3])} for r in rows])
    return samples, events


def normalize_events(events):
    """Overlay intervals from successive reports without double-counting or losing gaps."""
    grouped = {}
    for event in events:
        series = event["series"]
        remaining = []
        for old in grouped.get(series, []):
            if old["end"] <= event["start"] or old["start"] >= event["end"]:
                remaining.append(old)
            else:
                if old["start"] < event["start"]:
                    remaining.append({**old, "end": event["start"]})
                if old["end"] > event["end"]:
                    remaining.append({**old, "start": event["end"]})
        remaining.append(event)
        grouped[series] = remaining
    return sorted((e for rows in grouped.values() for e in rows), key=lambda e: (e["series"], e["start"]))


def decimate(samples, bucket_ms):
    """Keep first, last and both extrema (temperature AND humidity) in each absolute bucket."""
    if bucket_ms <= MINUTE:
        return samples
    buckets = {}
    for sample in samples:
        buckets.setdefault(sample[0] // bucket_ms, []).append(sample)
    result = []
    for values in buckets.values():
        chosen = {values[0][0]: values[0], values[-1][0]: values[-1]}
        for column in (1, 2):
            valid = [s for s in values if s[column] is not None]
            if valid:
                for s in (min(valid, key=lambda s: s[column]), max(valid, key=lambda s: s[column])):
                    chosen[s[0]] = s
        result.extend(sorted(chosen.values()))
    return result


def get_data(source, room, start, end):
    validate_source(source)
    window(start, end)
    if source == "demo":
        if room not in ROOM_NAMES:
            raise HTTPException(404, "Habitación desconocida")
        return demo_data(room, start, end)
    with database() as db:
        return real_data(db, room, start, end)


@app.get("/api/series")
def series(room: int, start: int, end: int, source: str = "real", buckets: int = Query(900, ge=100, le=1600)):
    samples, events = get_data(source, room, start, end)
    bucket_ms = max(MINUTE, math.ceil((end - start) / buckets / MINUTE) * MINUTE)
    # Mark actual acquisition gaps before reduction so they survive an overview.
    max_gap = (2 if source == "demo" else 25) * MINUTE
    gaps = [[a[0], b[0]] for a, b in zip(samples, samples[1:]) if b[0] - a[0] > max_gap]
    return {"source": source, "room": room, "start": start, "end": end,
            "bucketMs": bucket_ms, "rawCount": len(samples),
            "samples": decimate(samples, bucket_ms), "gaps": gaps, "events": events}


@app.get("/api/export.csv")
def export(room: int, start: int, end: int, source: str = "real"):
    samples, events = get_data(source, room, start, end)
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["source", "room_id", "timestamp_utc", "temperature_c", "humidity_pct"])
    writer.writerows([source, room, iso(t), temp, humidity] for t, temp, humidity in samples)
    return Response(stream.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="casa-{source}-{room}-{iso(start)[:10]}.csv"'})


@app.get("/api/summary")
def summary(room: int, start: int, end: int, source: str = "real"):
    samples, events = get_data(source, room, start, end)
    temperatures = [s[1] for s in samples if s[1] is not None]
    settings = sorted((e for e in events if e["series"] == "settings"), key=lambda e: e["start"])
    heat_ms = known_ms = 0
    for e in events:
        if e["series"] != "callForHeat":
            continue
        elapsed = max(0, min(end, e["end"]) - max(start, e["start"]))
        if e["value"] in HEAT_LEVELS:
            heat_ms += elapsed
        if e["value"] in HEAT_LEVELS or e["value"] == "NONE":
            known_ms += elapsed
    def target(e):
        v = e["value"]
        return v.get("temperature", {}).get("celsius") if v.get("power") == "ON" else None
    changes = [{"start": b["start"], "target": target(b)} for a, b in zip(settings, settings[1:])
               if start <= b["start"] < end and a["end"] == b["start"] and target(a) != target(b)]
    overshoot = 0
    i = 0
    for t, temp, _ in samples:
        while i < len(settings) and settings[i]["end"] <= t:
            i += 1
        if i < len(settings) and settings[i]["start"] <= t < settings[i]["end"]:
            goal = target(settings[i])
            if goal is not None and temp is not None:
                overshoot = max(overshoot, temp - goal)
    return {"source": source, "room": room, "start": start, "end": end, "count": len(temperatures),
            "min": min(temperatures) if temperatures else None, "max": max(temperatures) if temperatures else None,
            "change": temperatures[-1] - temperatures[0] if len(temperatures) > 1 else None,
            "heatMs": heat_ms, "knownMs": known_ms, "overshoot": overshoot, "changes": changes}


@app.get("/api/sessions")
def sessions(room: int, start: int, end: int, source: str = "real"):
    validate_source(source)
    window(start, end)
    # A bounded margin resolves most sessions crossing the viewport; remaining edges stay partial.
    a, b = max(0, start-DAY), end+DAY
    rooms = {}
    if source == "demo":
        if room not in ROOM_NAMES:
            raise HTTPException(404, "Habitación desconocida")
        for rid, name in ROOM_NAMES.items():
            # Only the selected room needs samples. Other rooms need state transitions.
            samples, events = demo_data(rid, a, b)
            rooms[rid] = {"name": name, "samples": samples if rid == room else [], "events": events}
    else:
        with database() as db:
            records = db.execute("SELECT room_id,name FROM rooms WHERE active=1").fetchall()
            if room not in [r[0] for r in records]:
                raise HTTPException(404, "Habitación desconocida")
            for rid, name in records:
                samples, events = real_data(db, rid, a, b)
                rooms[rid] = {"name": name, "samples": samples if rid == room else [], "events": events}
    return analyse(rooms, room, start, end, a, b, source)


@app.get("/api/invoices")
def invoices():
    # Explicit public fields only. No account identity, emails, tokens, or filesystem paths.
    if not INVOICES.exists():
        return {"invoices": [], "resolution": "billing_period", "completeHistory": False}
    try:
        if INVOICES.stat().st_size > 1024*1024:
            raise ValueError("Invoice file too large")
        data = json.loads(INVOICES.read_text())
        fields = ("period_start", "period_end", "billed_days", "consumption_kwh", "consumption_m3",
                  "fixed_eur", "variable_eur", "other_eur", "tax_eur", "total_eur")
        rows = [{key: row.get(key) for key in fields} for row in data['invoices']]
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(503, "Las facturas verificadas no están disponibles") from None
    return {"invoices": sorted(rows, key=lambda r:r['period_start'], reverse=True),
            "resolution": "billing_period", "completeHistory": False}


def demo_window_data(room, start, end):
    # Match the history's normal resolution; do not train on the minute-level demo chart.
    samples, events, previous = [], [], {}
    for t in range(math.ceil(max(start, DEMO_START) / (15 * MINUTE)) * 15 * MINUTE, min(end, DEMO_END) + 1, 15 * MINUTE):
        sample, target, heat = demo_sample(t, room)
        samples.append(sample)
        for series, value in [('connected', True), ('callForHeat', heat), ('settings', {'power': 'ON', 'temperature': {'celsius': target}})]:
            if series not in previous or previous[series]['value'] != value:
                if series in previous:
                    previous[series]['end'] = t
                previous[series] = {'series': series, 'value': value, 'start': t, 'end': min(end, DEMO_END)}
                events.append(previous[series])
    return samples, events


@app.get('/api/window-events')
def window_events(room: int, start: int, end: int, source: str = 'real'):
    validate_source(source)
    window(start, end)
    # UTC day-aligned padding makes identity independent of zoom/pan and supplies cooling baselines.
    a, b = max(0, start // DAY * DAY - 31 * DAY), (end // DAY + 2) * DAY
    snapshots, peers, outdoor, trial_samples, trial_events = [], [], [], [], []
    try:
        labels = window_store.labels(source, room)
        trials = window_store.experiments(room) if source == 'real' else []
        if source == 'demo':
            if room not in ROOM_NAMES:
                raise HTTPException(404, 'Habitación desconocida')
            samples, events = demo_window_data(room, a, b)
            peers = [demo_window_data(r, a, b)[0] for r in ROOM_NAMES if r != room]
        else:
            with database() as db:
                samples, events = real_data(db, room, a, b)
                for trial in trials:
                    if trial['opened'] and trial['closed']:
                        readings, states = real_data(db, room, trial['opened'] - 20 * MINUTE, trial['closed'] + 20 * MINUTE)
                        trial_samples.extend(readings)
                        trial_events.extend(states)
                home = db.execute('SELECT home_id FROM rooms WHERE room_id=? AND active=1', (room,)).fetchone()[0]
                # Read only the fields needed by the detector, never expose raw provider responses.
                snapshots = [{'at': ms(row[0]), 'openWindow': json.loads(row[1]).get('openWindow')}
                             for row in db.execute('SELECT observed_at,value_json FROM snapshots WHERE home_id=? AND room_id=? AND observed_at>=? AND observed_at<=? ORDER BY observed_at',
                                                   (home, room, iso(a), iso(b)))]
                for (rid,) in db.execute('SELECT room_id FROM rooms WHERE home_id=? AND active=1 AND room_id!=?', (home, room)):
                    peer_samples, peer_events = real_data(db, rid, a, b)
                    connected = Context(peer_events)
                    peers.append([[t, temp if connected.at('connected', t) is True else None, humidity]
                                  for t, temp, humidity in peer_samples])
            try:
                context = read_environment(a, b)
                # Weather is the exterior reference; an entrance sensor is not assumed outdoors.
                outdoor = [[r['timestamp'], r['temperature']] for r in context['weather']]
            except (sqlite3.Error, OSError):
                pass  # Optional context must not suppress an otherwise observable candidate.
        confirmed = guided_annotations(trials, sorted({s[0]: s for s in trial_samples}.values()), normalize_events(trial_events))
        result = detection(room, samples, events, start, end, snapshots=snapshots, peers=peers,
                           outdoor=outdoor, labels=labels, confirmed=confirmed)
        return {'source': source, 'room': room, 'start': start, 'end': end, **result}
    except (sqlite3.Error, ValueError, OSError):
        raise HTTPException(503, 'No se pudo analizar la ventilación') from None


class WindowFeedback(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: Literal['real', 'demo']
    room: int = Field(gt=0)
    eventId: str = Field(min_length=1, max_length=100)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    label: Literal['yes', 'no', 'unknown']


@app.post('/api/window-feedback')
def window_feedback(request: Request, body: WindowFeedback):
    origin = urlsplit(request.headers.get('origin', ''))
    if (request.headers.get('x-casa-feedback') != '1' or origin.scheme not in ('http', 'https')
            or origin.netloc != request.headers.get('host')
            or request.headers.get('sec-fetch-site', 'same-origin') != 'same-origin'):
        raise HTTPException(403, 'Origen no permitido')
    if body.end < body.start or body.end - body.start > DAY:
        raise HTTPException(422, 'Intervalo de evento no válido')
    data = window_events(body.room, max(0, body.start - MINUTE), body.end + MINUTE, body.source)
    event = next((e for e in data['events'] if e['id'] == body.eventId), None)
    if event is None:
        raise HTTPException(409, 'El evento ha cambiado. Actualiza el detalle.')
    try:
        window_store.save_label(body.source, body.room, event, body.label)
    except (sqlite3.Error, ValueError, OSError):
        raise HTTPException(503, 'No se pudo guardar la confirmación') from None
    return {'saved': True}


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")


if __name__ == "__main__":
    import logging
    from logging.handlers import RotatingFileHandler
    import uvicorn
    log_root = Path(os.environ.get("TADO_WEB_ROOT", Path.home() / "Library/Application Support/TadoWeb"))
    log_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(log_root / "server.log", maxBytes=512 * 1024, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.INFO)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("TADO_WEB_PORT", "8765")),
                log_config=None, access_log=False, proxy_headers=False)
