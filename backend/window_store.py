"""Private annotations/calibration storage, independent of the read-only climate history."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time


class WindowStore:
    def __init__(self, root):
        self.path = Path(root).expanduser().resolve() / 'window-insights.sqlite3'

    @contextmanager
    def connect(self, write=False):
        if not write and not self.path.exists():
            yield None
            return
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
            os.close(fd)
        db = sqlite3.connect(self.path if write else self.path.as_uri() + '?mode=ro', uri=not write, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute('PRAGMA foreign_keys=ON')
                db.execute('PRAGMA max_page_count=8192')  # 32 MiB; stop rather than erase labels.
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1):
                    raise ValueError('Unsupported window store version')
                db.executescript('''
                  CREATE TABLE IF NOT EXISTS feedback (
                    source TEXT, room INTEGER, event_id TEXT, label TEXT NOT NULL,
                    event_json TEXT NOT NULL, created INTEGER, updated INTEGER,
                    PRIMARY KEY(source,room,event_id));
                  CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY, room INTEGER, started INTEGER, expires INTEGER,
                    status TEXT, requests INTEGER DEFAULT 0, next_poll INTEGER,
                    opened INTEGER, closed INTEGER, fresh INTEGER DEFAULT 0,
                    confounded INTEGER DEFAULT 0);
                  CREATE TABLE IF NOT EXISTS observations (
                    session TEXT REFERENCES experiments(id), at INTEGER, value_json TEXT,
                    PRIMARY KEY(session,at));
                  PRAGMA user_version=1;
                ''')
                with db:
                    yield db
            else:
                db.execute('PRAGMA query_only=ON')
                if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                    raise ValueError('Unsupported window store version')
                yield db
        finally:
            db.close()

    def labels(self, source, room):
        with self.connect() as db:
            if db is None:
                return []
            return [{'id': r['event_id'], 'label': r['label'], 'event': json.loads(r['event_json']), 'created': r['created']}
                    for r in db.execute('SELECT * FROM feedback WHERE source=? AND room=? ORDER BY created', (source, room))]

    def save_label(self, source, room, event, label):
        if source not in ('real', 'demo') or label not in ('yes', 'no', 'unknown'):
            raise ValueError('Invalid annotation')
        now = int(time.time() * 1000)
        with self.connect(True) as db:
            db.execute('''INSERT INTO feedback VALUES (?,?,?,?,?,?,?)
              ON CONFLICT(source,room,event_id) DO UPDATE SET label=excluded.label,
              event_json=excluded.event_json,updated=excluded.updated''',
                       (source, room, event['id'], label, json.dumps(event), now, now))

    def experiments(self, room=None):
        with self.connect() as db:
            if db is None:
                return []
            rows = db.execute('SELECT * FROM experiments WHERE room=? ORDER BY started', (room,)) if room is not None else db.execute('SELECT * FROM experiments ORDER BY started')
            return [dict(r) for r in rows]

    def observations(self, session):
        with self.connect() as db:
            if db is None:
                return []
            return [json.loads(r[0]) for r in db.execute('SELECT value_json FROM observations WHERE session=? ORDER BY at', (session,))]
