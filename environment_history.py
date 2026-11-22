"""Local Hue observations and cached Local weather; independent of the Tado quota."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo
import fcntl
import json
import math
import os
import re
import shutil
import sqlite3
import ssl
import subprocess
import time
import urllib.parse
import urllib.request

ROOT = Path(os.environ.get("TADO_ENV_ROOT", Path.home() / "Library/Application Support/TadoEnvironment"))
AUTH = Path.home() / ".config/tado-experiment/hue-auth.json"
HISTORY = Path.home() / "Library/Application Support/TadoHistory/history.sqlite3"
TZ = ZoneInfo("Europe/Madrid")
HOUR = 3_600_000
DAY = 24 * HOUR


def connect(root=ROOT):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(root / "environment.sqlite3", timeout=5)
    db.execute("PRAGMA busy_timeout=5000")
    # Stop rather than delete history when full; keep room for an atomic backup.
    db.execute("PRAGMA max_page_count=8192")  # 32 MiB at the default page size
    db.executescript("""
        CREATE TABLE IF NOT EXISTS outdoor (
            timestamp INTEGER PRIMARY KEY, temperature REAL, reported_at INTEGER,
            sensor TEXT NOT NULL, valid INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS weather (
            timestamp INTEGER PRIMARY KEY, temperature REAL, precipitation REAL,
            code INTEGER, cloud REAL, is_day INTEGER, origin TEXT NOT NULL, fetched_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sun (
            day TEXT PRIMARY KEY, start INTEGER NOT NULL, end INTEGER NOT NULL,
            sunrise INTEGER, sunset INTEGER);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    return db


def state(db, key, default=None):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_state(db, key, value):
    db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))


def read_json(url, *, context=None, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, context=context, timeout=15) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Respuesta demasiado grande")
    return json.loads(raw)


def hue(cfg, kind, rid):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False  # Trust only the paired bridge certificate, not its DHCP address.
    ctx.load_verify_locations(cadata=cfg["certificate_pem"])
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    def fetch(ip):
        data = read_json(f"https://{ip}/clip/v2/resource/{kind}/{rid}", context=ctx,
                         headers={"hue-application-key": cfg["username"]})
        if data.get("errors") or len(data.get("data", [])) != 1:
            raise ValueError("Recurso Hue no disponible")
        return data["data"][0]
    try:
        return fetch(cfg["ip"])
    except (OSError, ValueError):
        # macOS getaddrinfo can fail for .local even when Bonjour sees the bridge.
        try:
            result = subprocess.run(["dns-sd", "-G", "v4", cfg["host"] + "."],
                                    capture_output=True, timeout=3)
            output = result.stdout
        except subprocess.TimeoutExpired as exc:
            output = exc.stdout or b""
        addresses = re.findall(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b", output)
        if not addresses:
            raise ValueError("Puente Hue no disponible") from None
        return fetch(addresses[-1].decode())


def store_outdoor(db, resource, connected, now):
    reading = resource.get("temperature", {})
    value = reading.get("temperature")
    valid = bool(connected and resource.get("enabled") and reading.get("temperature_valid")
                 and isinstance(value, (int, float)) and math.isfinite(value) and -80 <= value <= 80)
    changed = reading.get("temperature_report", {}).get("changed")
    reported = int(datetime.fromisoformat(changed.replace("Z", "+00:00")).timestamp() * 1000) if changed else None
    # timestamp is when we observed this valid state, not the sensor's last change time.
    db.execute("INSERT OR REPLACE INTO outdoor VALUES (?,?,?,?,?)",
               (now, value if valid else None, reported, "Entrada", int(valid)))
    set_state(db, "hue", {"updated": now, "ok": valid})


def collect_hue(db, now):
    cfg = json.loads(AUTH.read_text())
    resource = hue(cfg, "temperature", cfg["temperature_resource_id"])
    connection = hue(cfg, "zigbee_connectivity", cfg["connectivity_resource_id"])
    with db:
        store_outdoor(db, resource, connection.get("status") == "connected", now)


def store_weather(db, data, origin, now):
    hourly, daily = data["hourly"], data["daily"]
    # Validate before a transaction so malformed replies cannot partially replace the cache.
    fields = ["temperature_2m", "precipitation", "weather_code", "cloud_cover", "is_day"]
    if any(len(hourly[k]) != len(hourly["time"]) for k in fields):
        raise ValueError("Respuesta meteorológica incompleta")
    rows = []
    for i, stamp in enumerate(hourly["time"]):
        stamp *= 1000
        if stamp > now:  # A forecast is never inserted as a historical observation.
            continue
        values = [hourly[k][i] for k in fields]
        if all(v is None for v in values):
            continue
        rows.append((stamp, *values, origin, now))
    solar = []
    for i, stamp in enumerate(daily["time"]):
        day = datetime.fromtimestamp(stamp, TZ).date()
        start = datetime.combine(day, datetime.min.time(), TZ)
        end = start + timedelta(days=1)  # Local days can have 23 or 25 hours.
        sunrise, sunset = daily["sunrise"][i], daily["sunset"][i]
        solar.append((day.isoformat(), int(start.timestamp()*1000), int(end.timestamp()*1000),
                      int(sunrise*1000) if sunrise else None, int(sunset*1000) if sunset else None))
    if not rows:
        raise ValueError("Sin datos meteorológicos para este intervalo")
    with db:
        db.executemany("""INSERT INTO weather VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(timestamp) DO UPDATE SET
            temperature=coalesce(excluded.temperature,weather.temperature),
            precipitation=coalesce(excluded.precipitation,weather.precipitation),
            code=coalesce(excluded.code,weather.code), cloud=coalesce(excluded.cloud,weather.cloud),
            is_day=coalesce(excluded.is_day,weather.is_day), origin=excluded.origin, fetched_at=excluded.fetched_at""", rows)
        db.executemany("INSERT OR REPLACE INTO sun VALUES (?,?,?,?,?)", solar)


def fetch_weather(start, end, recent, now):
    cfg = json.loads((ROOT / "config.json").read_text())
    params = {"latitude": cfg["latitude"], "longitude": cfg["longitude"], "timezone": "Europe/Madrid",
              "timeformat": "unixtime", "start_date": str(start), "end_date": str(end),
              "hourly": "temperature_2m,precipitation,weather_code,cloud_cover,is_day",
              "daily": "sunrise,sunset"}
    base = "https://api.open-meteo.com/v1/forecast" if recent else "https://archive-api.open-meteo.com/v1/archive"
    return read_json(base + "?" + urllib.parse.urlencode(params))


def collect_weather(db, now):
    if now - state(db, "weather_attempt", 0) < 3 * HOUR:
        return
    with db:
        set_state(db, "weather_attempt", now)  # Also rate-limit failed attempts.
    today = datetime.fromtimestamp(now/1000, TZ).date()
    recent_start = today - timedelta(days=7)
    data = fetch_weather(recent_start, today, True, now)
    store_weather(db, data, "open-meteo:forecast:best_match", now)
    with db:
        set_state(db, "weather", {"updated": now, "ok": True})
    # Backfill only dates covered by Tado, at most 31 days per three-hour cycle.
    if HISTORY.exists():
        with closing(sqlite3.connect(f"file:{HISTORY}?mode=ro", uri=True)) as history:
            first = history.execute("SELECT min(timestamp) FROM samples").fetchone()[0]
        if first:
            start = datetime.fromisoformat(first.replace("Z", "+00:00")).astimezone(TZ).date() - timedelta(days=1)
            completed = state(db, "archive_through")
            if completed:
                start = max(start, datetime.fromisoformat(completed).date() + timedelta(days=1))
            end = min(recent_start-timedelta(days=1), start+timedelta(days=30))
            if start <= end:
                data = fetch_weather(start, end, False, now)
                store_weather(db, data, "open-meteo:archive:best_match", now)
                with db:
                    set_state(db, "archive_through", str(end))


def read_environment(start, end, root=ROOT, now=None):
    now = int(time.time()*1000) if now is None else now
    end = min(end, now)
    result = {"sensor": "Entrada", "outdoor": [], "weather": [], "days": [],
              "hueStatus": None, "weatherStatus": None, "firstOutdoor": None,
              "location": "Local", "timezone": "Europe/Madrid"}
    if not (root / "environment.sqlite3").exists():
        return result
    db = sqlite3.connect(f"file:{root / 'environment.sqlite3'}?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        samples = [list(r) for r in db.execute(
            "SELECT timestamp,temperature,reported_at FROM outdoor WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp",
            (start-15*60_000, end))]
        # Preserve extrema, first/last samples and every connectivity/acquisition gap.
        bucket = max(1, (end-start)//600)
        selected = {}
        groups = {}
        for i, row in enumerate(samples):
            groups.setdefault(row[0]//bucket, []).append(row)
            if row[1] is None or i == 0 or i == len(samples)-1:
                selected[row[0]] = row
            if i and row[0]-samples[i-1][0] > 15*60_000:
                previous = samples[i-1]
                selected[previous[0]] = previous
                selected[row[0]] = row
                middle = (previous[0]+row[0])//2
                selected[middle] = [middle, None, None]
        for rows in groups.values():
            valid = [r for r in rows if r[1] is not None]
            for row in [rows[0], rows[-1], *([min(valid, key=lambda r:r[1]), max(valid, key=lambda r:r[1])] if valid else [])]:
                selected[row[0]] = row
        result["outdoor"] = [selected[k] for k in sorted(selected)]
        result["weather"] = [dict(r) for r in db.execute(
            "SELECT timestamp,temperature,precipitation,code,cloud,is_day,origin FROM weather "
            "WHERE timestamp>=? AND timestamp<=? ORDER BY timestamp", (start-HOUR, end))]
        result["days"] = [dict(r) for r in db.execute(
            "SELECT * FROM sun WHERE end>? AND start<? ORDER BY start", (start, end))]
        result["firstOutdoor"] = db.execute("SELECT min(timestamp) FROM outdoor WHERE valid=1").fetchone()[0]
        result["hueStatus"] = state(db, "hue")
        result["weatherStatus"] = state(db, "weather")
    finally:
        db.close()
    return result


def main():
    os.umask(0o077)
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (ROOT / "collector.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        if shutil.disk_usage(ROOT).free < 1024**3:
            raise RuntimeError("Menos de 1 GiB disponible")
        now = int(time.time()*1000)
        db = connect()
        try:
            for name, collect in [("hue", collect_hue), ("weather", collect_weather)]:
                try:
                    collect(db, now)
                except Exception as exc:
                    # Never log request URLs/headers or credentials.
                    with db:
                        old = state(db, name, {})
                        set_state(db, name, {**old, "ok": False, "failedAt": now, "error": type(exc).__name__,
                                             "reason": str(getattr(exc, "reason", ""))[:160]})
                        if name == "hue":
                            db.execute("INSERT OR REPLACE INTO outdoor VALUES (?,?,?,?,?)", (now, None, None, "Entrada", 0))
            if now - state(db, "backup_at", 0) >= DAY:
                temp = ROOT / "backup.tmp.sqlite3"
                with closing(sqlite3.connect(temp)) as backup:
                    db.backup(backup)
                    if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise RuntimeError("Respaldo no válido")
                temp.replace(ROOT / "backup.sqlite3")
                with db:
                    set_state(db, "backup_at", now)
            print(json.dumps({name: state(db, name) for name in ["hue", "weather"]}))
        finally:
            db.close()


if __name__ == "__main__":
    main()
