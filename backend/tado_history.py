#!/usr/bin/env python3
"""Read-only Tado X history collector. Python standard library only."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import fcntl
import gzip
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path.home() / "Library/Application Support/TadoHistory"
UTC = timezone.utc
MIB = 1024 * 1024
SCHEMA_VERSION = 1
DEFAULTS = {
    "home_id": None,
    "client_id": "",
    "timezone": "Europe/Madrid",
    "slots": ["06:05", "12:05", "18:05", "23:55"],
    "auth_path": str(Path.home() / ".config/tado-experiment/auth.json"),
    "storage_limit_bytes": 256 * MIB,
    "database_limit_bytes": 64 * MIB,
    "minimum_free_bytes": 1024 * MIB,
    "daily_request_limit": 80,
    "server_quota_reserve": 20,
    "max_attempts_per_slot": 3,
    "retry_seconds": 120,
}


class CollectorError(Exception):
    pass


class APIError(CollectorError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"Tado HTTP {status}")


def utcnow():
    return datetime.now(UTC)


def stamp(value=None):
    return (value or utcnow()).astimezone(UTC).isoformat(timespec="milliseconds")


def parse(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise CollectorError("Timestamp without timezone")
    return result.astimezone(UTC)


def atomic_json(path: Path, value):
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def storage_bytes(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def disk_guard(root, config, extra=0):
    # Reserve room for SQLite's rollback journal and bounded HTTP responses.
    database = root / "history.sqlite3"
    size = database.stat().st_size if database.exists() else 0
    reserve = max(size, 8 * MIB) + extra
    if storage_bytes(root) + reserve > config["storage_limit_bytes"]:
        raise CollectorError("Paused: local storage budget reached; history preserved")
    if shutil.disk_usage(root).free < config["minimum_free_bytes"] + reserve:
        raise CollectorError("Paused: insufficient free disk space; history preserved")


def connect(root, config):
    db = sqlite3.connect(root / "history.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("PRAGMA synchronous=FULL")
    page_size = db.execute("PRAGMA page_size").fetchone()[0]
    db.execute(f"PRAGMA max_page_count={config['database_limit_bytes'] // page_size}")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise CollectorError("Unsupported database schema; update collector")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS rooms (
        home_id INTEGER NOT NULL, room_id INTEGER NOT NULL, name TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(home_id,room_id));
      CREATE TABLE IF NOT EXISTS reports (
        home_id INTEGER NOT NULL, room_id INTEGER NOT NULL, day TEXT NOT NULL,
        coverage_start TEXT NOT NULL, coverage_end TEXT NOT NULL,
        fetched_at TEXT NOT NULL, slot TEXT NOT NULL, raw_gzip BLOB NOT NULL,
        PRIMARY KEY(home_id,room_id,day));
      CREATE TABLE IF NOT EXISTS samples (
        home_id INTEGER NOT NULL, room_id INTEGER NOT NULL, timestamp TEXT NOT NULL,
        temperature_c REAL, humidity_pct REAL,
        PRIMARY KEY(home_id,room_id,timestamp));
      CREATE TABLE IF NOT EXISTS events (
        home_id INTEGER NOT NULL, room_id INTEGER NOT NULL, day TEXT NOT NULL,
        series TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, value_json TEXT NOT NULL,
        PRIMARY KEY(home_id,room_id,day,series,start));
      CREATE INDEX IF NOT EXISTS events_time ON events(home_id,room_id,start,end);
      CREATE TABLE IF NOT EXISTS device_changes (
        id INTEGER PRIMARY KEY, home_id INTEGER NOT NULL, serial TEXT NOT NULL,
        observed_at TEXT NOT NULL, value_json TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS device_latest ON device_changes(home_id,serial,id);
      CREATE TABLE IF NOT EXISTS snapshots (
        home_id INTEGER NOT NULL, room_id INTEGER NOT NULL, observed_at TEXT NOT NULL,
        value_json TEXT NOT NULL, PRIMARY KEY(home_id,room_id,observed_at));
      CREATE TABLE IF NOT EXISTS runs (
        day TEXT NOT NULL, slot TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt TEXT, completed_at TEXT, error TEXT,
        PRIMARY KEY(day,slot));
      CREATE TABLE IF NOT EXISTS request_counts (day TEXT PRIMARY KEY,count INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
    """)
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    db.commit()
    return db


def state_get(db, key, default=None):
    row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def state_set(db, key, value):
    db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))
    db.commit()


def due_slot(db, config, now):
    local = now.astimezone(ZoneInfo(config["timezone"]))
    day = local.date().isoformat()
    eligible = [slot for slot in config["slots"] if slot <= local.strftime("%H:%M")]
    # A full-day report supersedes missed earlier runs; never replay yesterday.
    if not eligible and state_get(db, "last_success"):
        return None
    slot = eligible[-1] if eligible else "startup"
    row = db.execute("SELECT * FROM runs WHERE day=? AND slot=?", (day, slot)).fetchone()
    if row:
        if row["completed_at"] or row["attempts"] >= config["max_attempts_per_slot"]:
            return None
        if row["last_attempt"] and (now - parse(row["last_attempt"])).total_seconds() < config["retry_seconds"]:
            return None
    return day, slot


def normalize_report(data):
    if not isinstance(data, dict) or not isinstance(data.get("measuredData"), dict):
        raise CollectorError("Invalid day report")
    coverage = data.get("interval", {})
    start, end = stamp(parse(coverage["from"])), stamp(parse(coverage["to"]))
    if start >= end:
        raise CollectorError("Empty report coverage")
    samples = {}
    for series, field in [("insideTemperature", "temperature_c"), ("humidity", "humidity_pct")]:
        points = data["measuredData"].get(series, {}).get("dataPoints")
        if not isinstance(points, list):
            raise CollectorError(f"Missing {series} series")
        for point in points:
            ts = stamp(parse(point["timestamp"]))
            value = point["value"]
            if value is None:
                continue
            value = value.get("celsius") if field == "temperature_c" else value * 100
            if value is not None:
                samples.setdefault(ts, {})[field] = float(value)
    events = []
    series_values = {k: data.get(k) for k in ["settings", "callForHeat", "stripes"]}
    series_values["connected"] = data["measuredData"].get("measuringDeviceConnected")
    for name, series in series_values.items():
        if not isinstance(series, dict) or not isinstance(series.get("dataIntervals"), list):
            raise CollectorError(f"Missing {name} intervals")
        for interval in series["dataIntervals"]:
            a, b = stamp(parse(interval["from"])), stamp(parse(interval["to"]))
            if a >= b:
                continue
            events.append((name, a, b, json.dumps(interval["value"], sort_keys=True)))
    return start, end, samples, events


def persist_report(db, home, room, day, slot, data):
    start, end, samples, events = normalize_report(data)
    old = db.execute("SELECT * FROM reports WHERE home_id=? AND room_id=? AND day=?", (home, room, day)).fetchone()
    if old and (start > old["coverage_start"] or end < old["coverage_end"]):
        raise CollectorError("Regressive report rejected; previous coverage preserved")
    # Do not let an apparently longer report silently remove known measurements.
    if old:
        old_data = json.loads(gzip.decompress(old["raw_gzip"]))
        _, _, prior_samples, prior_events = normalize_report(old_data)
        for ts, values in prior_samples.items():
            missing = set(values) - set(samples.get(ts, {}))
            if not missing:
                continue
            # Tado's moving terminal observation can disappear from later reports.
            # Accept only that edge when every missing field has newer measurements;
            # the original SQLite sample stays intact (upserts below never delete it).
            moving_end = (ts == old["coverage_end"] and end > old["coverage_end"]
                          and all(any(t > ts and field in v for t, v in samples.items())
                                  for field in missing))
            if not moving_end:
                raise CollectorError("Incomplete report rejected; previous samples preserved")
        for series in {event[0] for event in prior_events}:
            prior = [x for x in prior_events if x[0] == series]
            new = sorted((x for x in events if x[0] == series), key=lambda x: x[1])
            for previous in prior:
                covered = previous[1]
                for interval in new:
                    if interval[1] > covered:
                        break
                    if interval[2] > covered:
                        covered = interval[2]
                    if covered >= previous[2]:
                        break
                if covered < previous[2]:
                    raise CollectorError("Incomplete report intervals rejected")
    raw = gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0)
    with db:
        for ts, values in samples.items():
            db.execute("""INSERT INTO samples VALUES (?,?,?,?,?) ON CONFLICT(home_id,room_id,timestamp)
              DO UPDATE SET temperature_c=COALESCE(excluded.temperature_c,samples.temperature_c),
              humidity_pct=COALESCE(excluded.humidity_pct,samples.humidity_pct)""",
              (home, room, ts, values.get("temperature_c"), values.get("humidity_pct")))
        db.execute("DELETE FROM events WHERE home_id=? AND room_id=? AND day=?", (home, room, day))
        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", [(home, room, day, *event) for event in events])
        db.execute("INSERT OR REPLACE INTO reports VALUES (?,?,?,?,?,?,?,?)", (home, room, day, start, end, stamp(), slot, raw))


class API:
    def __init__(self, db, config):
        self.db, self.config = db, config
        self.auth_path = Path(config["auth_path"])
        self.auth = json.loads(self.auth_path.read_text())

    def reserve_request(self):
        now = utcnow()
        blocked = state_get(self.db, "api_blocked_until")
        if blocked and parse(blocked) > now:
            raise CollectorError("Tado quota pause is active")
        day = now.date().isoformat()
        with self.db:
            row = self.db.execute("SELECT count FROM request_counts WHERE day=?", (day,)).fetchone()
            count = row[0] if row else 0
            if count >= self.config["daily_request_limit"]:
                raise CollectorError("Local daily request limit reached")
            self.db.execute("INSERT OR REPLACE INTO request_counts VALUES (?,?)", (day, count + 1))

    def headers(self, headers, status=200):
        value = headers.get("ratelimit", "")
        remaining = re.search(r"(?:^|;)\s*r=(\d+)", value)
        reset = re.search(r"(?:^|;)\s*t=(\d+)", value)
        if remaining:
            state_set(self.db, "server_quota", {"remaining": int(remaining[1]), "policy": headers.get("ratelimit-policy"), "observed_at": stamp()})
        if status == 429 or (remaining and int(remaining[1]) <= self.config["server_quota_reserve"]):
            retry = headers.get("Retry-After", "")
            seconds = int(reset[1]) if reset else int(retry) if retry.isdigit() else 86400
            state_set(self.db, "api_blocked_until", stamp(utcnow() + timedelta(seconds=max(120, seconds))))

    def refresh(self):
        client_id = self.config.get("client_id")
        if not client_id:
            raise CollectorError("Set client_id in the private collector configuration")
        self.reserve_request()
        body = urllib.parse.urlencode({"client_id": client_id, "grant_type": "refresh_token", "refresh_token": self.auth["refresh_token"]}).encode()
        request = urllib.request.Request("https://login.tado.com/oauth2/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                updated = json.loads(response.read(1024 * 1024))
        except urllib.error.HTTPError as exc:
            self.headers(exc.headers, exc.code)
            raise CollectorError(f"Authentication refresh failed (HTTP {exc.code}); reauthorization may be required") from None
        if not updated.get("access_token"):
            raise CollectorError("Authentication response has no token")
        self.auth.update(updated)
        self.auth["obtained_at"] = time.time()
        atomic_json(self.auth_path, self.auth)

    def get(self, host, path):
        if host not in ("hops.tado.com", "my.tado.com") or not path.startswith("/"):
            raise CollectorError("Unexpected API destination")
        expires = self.auth.get("obtained_at", 0) + self.auth.get("expires_in", 600)
        if time.time() >= expires - 60:
            self.refresh()
        for attempt in range(3):
            self.reserve_request()
            request = urllib.request.Request("https://" + host + path, headers={"Authorization": "Bearer " + self.auth["access_token"], "Accept": "application/json", "Accept-Encoding": "gzip"})
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    self.headers(response.headers)
                    raw = response.read(2 * MIB + 1)
                    if len(raw) > 2 * MIB:
                        raise CollectorError("Oversized API response")
                    if response.headers.get("Content-Encoding") == "gzip":
                        import io
                        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
                            raw = compressed.read(4 * MIB + 1)
                        if len(raw) > 4 * MIB:
                            raise CollectorError("Oversized decompressed API response")
                    return json.loads(raw)
            except urllib.error.HTTPError as exc:
                self.headers(exc.headers, exc.code)
                if exc.code == 401 and attempt == 0:
                    self.refresh()
                    continue
                if exc.code in (408, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise APIError(exc.code) from None
            except (urllib.error.URLError, TimeoutError):
                if attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise CollectorError("Network unavailable after bounded retries") from None
        raise CollectorError("Authentication failed")


def backup(db, root, config, day):
    if state_get(db, "backup_day") == day:
        return
    disk_guard(root, config, extra=(root / "history.sqlite3").stat().st_size)
    target = root / "backup.sqlite3.tmp"
    try:
        with sqlite3.connect(target) as copy:
            db.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise CollectorError("Backup validation failed")
        os.replace(target, root / "backup.sqlite3")
        state_set(db, "backup_day", day)
    finally:
        if target.exists():
            target.unlink()


def collect(db, root, config, force=False):
    now = utcnow()
    slot_info = due_slot(db, config, now)
    if force:
        slot_info = (now.astimezone(ZoneInfo(config["timezone"])).date().isoformat(), "manual-" + now.strftime("%H%M%S"))
    if not slot_info:
        return False
    day, slot = slot_info
    if not isinstance(config.get("home_id"), int) or config["home_id"] <= 0:
        raise CollectorError("Set home_id in the private collector configuration")
    disk_guard(root, config)
    with db:
        db.execute("""INSERT INTO runs(day,slot,attempts,last_attempt) VALUES (?,?,1,?)
          ON CONFLICT(day,slot) DO UPDATE SET attempts=attempts+1,last_attempt=excluded.last_attempt,error=NULL""", (day, slot, stamp(now)))
    try:
        api = API(db, config)
        home = config["home_id"]
        rooms = api.get("hops.tado.com", f"/homes/{home}/rooms")
        if not isinstance(rooms, list) or not rooms or any(not isinstance(room, dict) or not isinstance(room.get("id"), int) or not isinstance(room.get("name"), str) for room in rooms):
            raise CollectorError("Invalid room list")
        with db:
            db.execute("UPDATE rooms SET active=0 WHERE home_id=?", (home,))
            for room in rooms:
                db.execute("INSERT OR REPLACE INTO rooms VALUES (?,?,?,1)", (home, room["id"], room["name"].strip()))
                db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)", (home, room["id"], stamp(now), json.dumps(room, sort_keys=True)))
        errors = []
        for room in rooms:
            saved = db.execute("SELECT slot FROM reports WHERE home_id=? AND room_id=? AND day=?", (home, room["id"], day)).fetchone()
            if saved and saved[0] == slot:
                continue
            if utcnow().astimezone(ZoneInfo(config["timezone"])).date().isoformat() != day:
                raise CollectorError("Day changed during collection; retry the current day")
            disk_guard(root, config)
            try:
                report = api.get("my.tado.com", f"/api/v2/homes/{home}/zones/{room['id']}/dayReport?date={day}")
                persist_report(db, home, room["id"], day, slot, report)
            except APIError as exc:
                if exc.status in (401, 429):
                    raise
                errors.append(f"room {room['id']}: HTTP {exc.status}")
        if errors:
            raise CollectorError("; ".join(errors))
        # Daily device metadata is optional; its failure must not lose climate data.
        if state_get(db, "devices_day") != day:
            try:
                devices = api.get("hops.tado.com", f"/homes/{home}/roomsAndDevices")
                flattened = [(room["roomId"], device) for room in devices["rooms"] for device in room["devices"]]
                flattened.extend((None, device) for device in devices.get("otherDevices", []))
                with db:
                    for room_id, device in flattened:
                        # Room reports contain temperature; don't duplicate it in metadata.
                        value = {k: v for k, v in device.items() if k != "temperatureAsMeasured"}
                        value["room_id"] = room_id
                        encoded = json.dumps(value, sort_keys=True)
                        serial = device["serialNumber"]
                        old = db.execute("SELECT value_json FROM device_changes WHERE home_id=? AND serial=? ORDER BY id DESC LIMIT 1", (home, serial)).fetchone()
                        if not old or old[0] != encoded:
                            db.execute("INSERT INTO device_changes(home_id,serial,observed_at,value_json) VALUES (?,?,?,?)", (home, serial, stamp(), encoded))
                state_set(db, "devices_day", day)
                state_set(db, "metadata_warning", None)
            except (CollectorError, KeyError, TypeError, urllib.error.URLError) as exc:
                state_set(db, "metadata_warning", safe_error(exc))
        with db:
            db.execute("UPDATE runs SET completed_at=?,error=NULL WHERE day=? AND slot=?", (stamp(), day, slot))
        state_set(db, "last_success", stamp())
        state_set(db, "last_error", None)
        backup(db, root, config, day)
        logging.info("Saved history for %d rooms, %s %s", len(rooms), day, slot)
        return True
    except Exception as exc:
        message = safe_error(exc)
        with db:
            db.execute("UPDATE runs SET error=? WHERE day=? AND slot=?", (message, day, slot))
        state_set(db, "last_error", message)
        raise


def safe_error(exc):
    if isinstance(exc, CollectorError):
        return str(exc)
    if isinstance(exc, urllib.error.URLError):
        return "Network unavailable or request timed out"
    if isinstance(exc, sqlite3.Error):
        return "SQLite error: " + str(exc)
    return type(exc).__name__ + ": collection failed (no credentials logged)"


def status(db, root, config):
    today = utcnow().astimezone(ZoneInfo(config["timezone"])).date().isoformat()
    reports = [dict(row) for row in db.execute("""SELECT r.name,p.room_id,p.day,p.coverage_start,p.coverage_end,p.fetched_at
      FROM reports p JOIN rooms r USING(home_id,room_id) WHERE p.day=? ORDER BY r.name""", (today,))]
    disk_warning = None
    try:
        disk_guard(root, config)
    except CollectorError as exc:
        disk_warning = str(exc)
    return {"schema_version": SCHEMA_VERSION, "checked_at": stamp(), "timezone": config["timezone"],
            "last_success": state_get(db, "last_success"), "last_error": state_get(db, "last_error"),
            "storage_warning": disk_warning,
            "metadata_warning": state_get(db, "metadata_warning"),
            "storage_bytes": storage_bytes(root), "storage_limit_bytes": config["storage_limit_bytes"],
            "database_limit_bytes": config["database_limit_bytes"], "free_disk_bytes": shutil.disk_usage(root).free,
            "quota": state_get(db, "server_quota"), "api_blocked_until": state_get(db, "api_blocked_until"),
            "requests_today": db.execute("SELECT COALESCE(SUM(count),0) FROM request_counts WHERE day=?", (utcnow().date().isoformat(),)).fetchone()[0],
            "daily_request_limit": config["daily_request_limit"], "collection_times": config["slots"],
            "sample_rows": db.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
            "report_days": db.execute("SELECT COUNT(DISTINCT day) FROM reports").fetchone()[0], "today": reports}


def export(db, config, day, kind):
    local = ZoneInfo(config["timezone"])
    start = datetime.fromisoformat(day).replace(tzinfo=local)
    end = start + timedelta(days=1)
    writer = csv.writer(sys.stdout)
    if kind == "samples":
        writer.writerow(["room", "timestamp_local", "timestamp_utc", "temperature_c", "humidity_pct"])
        rows = db.execute("""SELECT r.name,s.* FROM samples s JOIN rooms r USING(home_id,room_id)
          WHERE timestamp>=? AND timestamp<? ORDER BY timestamp,r.name""", (stamp(start), stamp(end)))
        for row in rows:
            writer.writerow([row["name"], parse(row["timestamp"]).astimezone(local).isoformat(), row["timestamp"], row["temperature_c"], row["humidity_pct"]])
    elif kind == "events":
        writer.writerow(["room", "series", "start_local", "end_local", "value_json"])
        rows = db.execute("""SELECT r.name,e.* FROM events e JOIN rooms r USING(home_id,room_id)
          WHERE day=? ORDER BY start,r.name,series""", (day,))
        for row in rows:
            a, b = max(parse(row["start"]), start), min(parse(row["end"]), end)
            if a < b:
                writer.writerow([row["name"], row["series"], a.astimezone(local).isoformat(), b.astimezone(local).isoformat(), row["value_json"]])
    else:
        rows = db.execute("SELECT room_id,day,raw_gzip FROM reports WHERE day=?", (day,))
        print(json.dumps({"schema_version": SCHEMA_VERSION, "timezone": config["timezone"], "day": day,
            "rooms": [{"room_id": r["room_id"], "report": json.loads(gzip.decompress(r["raw_gzip"]))} for r in rows]}, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--force", action="store_true")
    sub.add_parser("status")
    export_parser = sub.add_parser("export")
    export_parser.add_argument("--day", required=True)
    export_parser.add_argument("--kind", choices=["samples", "events", "json"], default="samples")
    args = parser.parse_args(argv)
    if args.command == "collect":
        def timeout_handler(signum, frame):
            raise CollectorError("Collection deadline exceeded; retry is bounded")
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(180)
    os.umask(0o077)
    root = args.root.expanduser()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    config = DEFAULTS | (json.loads((root / "config.json").read_text()) if (root / "config.json").exists() else {})
    handler = RotatingFileHandler(root / "collector.log", maxBytes=MIB, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    db = None
    with (root / "collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Collector already running", file=sys.stderr)
            return 0
        try:
            if args.command == "collect":
                disk_guard(root, config)
            db = connect(root, config)
            if args.command == "collect":
                collect(db, root, config, args.force)
                atomic_json(root / "status.json", status(db, root, config))
            elif args.command == "status":
                print(json.dumps(status(db, root, config), ensure_ascii=False, indent=2))
            elif args.command == "export":
                datetime.strptime(args.day, "%Y-%m-%d")
                export(db, config, args.day, args.kind)
            return 0
        except Exception as exc:
            message = safe_error(exc)
            logging.error(message)
            # One bounded health file, including when the database cannot be written.
            try:
                atomic_json(root / "status.json", {"checked_at": stamp(), "last_error": message, "paused": True})
            except OSError:
                pass
            print(message, file=sys.stderr)
            return 1
        finally:
            if db is not None:
                db.close()


if __name__ == "__main__":
    raise SystemExit(main())
