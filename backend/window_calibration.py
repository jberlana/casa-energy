"""Opt-in guided calibration: read room snapshots only, never change heating.

Run from the repository root: python -m backend.window_calibration --help.
No background job is installed. An expired session can never be resumed.
"""
import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import fcntl
import json
import os
import sqlite3
from pathlib import Path
import time
import uuid

from . import tado_history as tado
from .window_detection import MINUTE, DAY, Context, detection, finite, guided_annotations, room_threshold
from .window_store import WindowStore

LIMIT = 20
POLL_MS = 2 * MINUTE
LIFETIME = 30 * MINUTE


def safe_reading(room, now):
    points = room.get('sensorDataPoints') or {}
    temperature = points.get('insideTemperature') or {}
    humidity = points.get('humidity') or {}
    raw_time = temperature.get('timestamp')
    try:
        measured = int(tado.parse(raw_time).timestamp() * 1000) if raw_time else None
    except (ValueError, TypeError, AttributeError):
        measured = None
    target = (room.get('setting') or {}).get('temperature')
    if isinstance(target, dict):
        target = target.get('value', target.get('celsius'))
    window = room.get('openWindow')
    return {'at': now, 'measuredAt': measured,
            'temperature': temperature.get('value') if finite(temperature.get('value')) else None,
            'humidity': humidity.get('percentage') if finite(humidity.get('percentage')) else None,
            'target': target if finite(target) else None,
            'power': (room.get('setting') or {}).get('power'),
            'heating': (room.get('heatingPower') or {}).get('percentage'),
            'connected': (room.get('connection') or {}).get('state') in ('CONNECTED', 'ONLINE'),
            'openWindow': {'detected': True, 'activated': window.get('activated')} if isinstance(window, dict) and window else None}


class Calibration:
    def __init__(self, history, store, clock=lambda: int(time.time() * 1000)):
        self.history = Path(history).expanduser().resolve()
        self.store = store
        self.clock = clock

    def state(self, session):
        row = next((r for r in self.store.experiments() if r['id'] == session), None)
        if row is None:
            raise ValueError('Unknown calibration session')
        if row['status'] == 'active' and self.clock() >= row['expires']:
            row['status'] = 'expired'
        readings = self.store.observations(session)
        row['observations'] = len(readings)
        row['freshness'] = 'verified' if row['fresh'] else 'unverified'
        recent_fresh = any(r.get('fresh') and 0 <= self.clock() - r['at'] <= 4 * MINUTE for r in readings)
        row['canOpen'] = bool(row['status'] == 'active' and row['fresh'] and not row['opened']
                              and self.clock() >= row['started'] + 10 * MINUTE
                              and self.clock() <= row['started'] + 14 * MINUTE
                              and recent_fresh and readings and self.clock() - readings[-1]['at'] <= 3 * MINUTE)
        row['instruction'] = ('Session ended; close the window if it is still open.' if row['status'] != 'active' else
                              'Observe recovery; no further action.' if row['closed'] else
                              'Close after about five minutes, or earlier if needed; confirm with mark closed.' if row['opened'] else
                              'Ready: open the window normally and confirm with mark opened.' if row['canOpen'] else
                              'Keep the window closed. Collecting baseline and checking freshness.')
        return row

    @contextmanager
    def collector(self):
        # Reuse the real collector's lock so tokens, quota counters and SQLite have a single writer.
        if not (self.history / 'config.json').exists() or not (self.history / 'history.sqlite3').exists():
            raise ValueError('Configure the collector before calibration')
        with (self.history / 'collector.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            config = tado.DEFAULTS | json.loads((self.history / 'config.json').read_text())
            tado.disk_guard(self.history, config)
            db = tado.connect(self.history, config)
            try:
                yield db, config
            finally:
                db.close()

    def quota(self, db, cfg, needed):
        now = self.clock()
        blocked = tado.state_get(db, 'api_blocked_until')
        if blocked and tado.parse(blocked).timestamp() * 1000 > now:
            raise ValueError('Tado quota pause is active')
        rooms = db.execute('SELECT count(*) FROM rooms WHERE active=1').fetchone()[0]
        scheduled = len(cfg['slots']) * (rooms + 3)
        day = datetime.fromtimestamp(now / 1000, timezone.utc).date().isoformat()
        used = db.execute('SELECT coalesce(sum(count),0) FROM request_counts WHERE day=?', (day,)).fetchone()[0]
        if used + scheduled + needed > cfg['daily_request_limit']:
            raise ValueError('Insufficient daily quota after scheduled reserve')
        server = tado.state_get(db, 'server_quota')
        if not server or not isinstance(server.get('remaining'), int) or not server.get('observed_at'):
            raise ValueError('A recent regular collection must establish the server quota first')
        age = now - int(tado.parse(server['observed_at']).timestamp() * 1000)
        if age < 0 or age > DAY:
            raise ValueError('Server quota observation is stale')
        if server['remaining'] < scheduled + needed + cfg['server_quota_reserve']:
            raise ValueError('Insufficient server quota after reserve')

    def start(self, room):
        now = self.clock()
        with self.collector() as (db, cfg):
            if not db.execute('SELECT 1 FROM rooms WHERE room_id=? AND active=1', (room,)).fetchone():
                raise ValueError('Unknown room')
            self.quota(db, cfg, LIMIT)
            with self.store.connect(True) as state:
                state.execute("UPDATE experiments SET status='expired' WHERE status='active' AND expires<=?", (now,))
                if state.execute("SELECT 1 FROM experiments WHERE status='active'").fetchone():
                    raise ValueError('Another calibration is active')
                session = uuid.uuid4().hex
                state.execute('INSERT INTO experiments(id,room,started,expires,status,next_poll) VALUES (?,?,?,?,?,?)',
                              (session, room, now, now + LIFETIME, 'active', now))
        return self.state(session)

    def mark(self, session, action):
        row = self.state(session)
        now = self.clock()
        if row['status'] != 'active':
            raise ValueError('Calibration is no longer active')
        if action == 'opened' and not row['canOpen']:
            raise ValueError('Baseline or freshness is insufficient; keep the window closed')
        if action == 'closed' and (not row['opened'] or row['closed']):
            raise ValueError('No open window awaiting closure')
        with self.store.connect(True) as db:
            if action in ('opened', 'closed'):
                db.execute(f'UPDATE experiments SET {action}=? WHERE id=?', (now, session))
            elif action == 'confounded':
                db.execute('UPDATE experiments SET confounded=1 WHERE id=?', (session,))
            elif action == 'stop':
                db.execute("UPDATE experiments SET status='stopped' WHERE id=?", (session,))
            else:
                raise ValueError('Unknown action')
        return self.state(session)

    def tick(self, session):
        row = self.state(session)
        now = self.clock()
        if row['status'] != 'active' or now < row['next_poll']:
            return row
        calibration = self

        class BoundedAPI(tado.API):
            def reserve_request(self):
                state = calibration.state(session)
                if state['status'] != 'active' or state['requests'] >= LIMIT:
                    raise ValueError('Calibration deadline or request limit reached')
                calibration.quota(self.db, self.config, 1)
                # Charge each attempt before network access, including refresh and retries.
                with calibration.store.connect(True) as ledger:
                    ledger.execute('UPDATE experiments SET requests=requests+1 WHERE id=?', (session,))
                super().reserve_request()

        try:
            with self.collector() as (db, cfg):
                # Recheck under the cross-process lock; concurrent ticks cannot double-poll.
                row = self.state(session)
                if row['status'] != 'active' or now < row['next_poll']:
                    return row
                with self.store.connect(True) as ledger:
                    ledger.execute('UPDATE experiments SET next_poll=? WHERE id=?', (now + POLL_MS, session))
                rooms = BoundedAPI(db, cfg).get('hops.tado.com', f"/homes/{cfg['home_id']}/rooms")
                room = next((r for r in rooms if r.get('id') == row['room']), None)
                if room is None:
                    raise ValueError('Selected room missing from response')
                observed = self.clock()
                if observed >= row['expires']:
                    return self.state(session)
                reading = safe_reading(room, observed)
                previous = self.store.observations(session)
                # A changed value establishes a new observation; equal values with no timestamp
                # do not. Never advertise polling frequency as sensor measurement resolution.
                stamped = reading['measuredAt'] is not None and 0 <= observed - reading['measuredAt'] <= 3 * MINUTE
                changed = bool(previous and finite(previous[-1]['temperature']) and reading['measuredAt'] is None
                               and reading['temperature'] != previous[-1]['temperature'])
                fresh = bool(reading['connected'] and finite(reading['temperature']) and
                             ((stamped and (not previous or reading['measuredAt'] != previous[-1]['measuredAt'])) or changed))
                reading['fresh'] = fresh
                with self.store.connect(True) as ledger:
                    ledger.execute('INSERT INTO observations VALUES (?,?,?)', (session, observed, json.dumps(reading)))
                    # Need two fresh observations during the baseline, not a single cached reading.
                    verified = sum(bool(r.get('fresh')) for r in [*previous, reading]) >= 2
                    ledger.execute('UPDATE experiments SET fresh=? WHERE id=?', (int(verified), session))
                    if row['requests'] + 1 >= LIMIT:
                        ledger.execute("UPDATE experiments SET status='limit' WHERE id=?", (session,))
                    if not row['opened'] and observed >= row['started'] + 14 * MINUTE:
                        ledger.execute("UPDATE experiments SET status='insufficient' WHERE id=?", (session,))
        except BlockingIOError:
            return self.state(session)  # Normal collector owns the lock; it takes priority.
        except Exception:
            with self.store.connect(True) as ledger:
                ledger.execute("UPDATE experiments SET status='failed' WHERE id=? AND status='active'", (session,))
            raise
        return self.state(session)

    def report(self, room):
        """Compare native history with experiment readings; training cases are never scored as holdouts."""
        from .web_server import normalize_events, real_data
        trials = [r for r in self.store.experiments(room) if r['opened'] and r['closed']]
        cases = []
        with closing(sqlite3.connect((self.history / 'history.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('PRAGMA query_only=ON')
            for trial in trials:
                samples, intervals = real_data(db, room, trial['started'] - 20 * MINUTE, trial['expires'] + 20 * MINUTE)
                cases.append((trial, samples, intervals))
        annotations = guided_annotations(trials, sorted({s[0]: s for _, samples, _ in cases for s in samples}.values()),
                                         normalize_events([e for _, _, intervals in cases for e in intervals]))
        annotations = {e['id']: e for e in [*annotations, *self.store.labels('real', room)]}
        threshold, training = room_threshold(annotations.values())
        trained = [e for e in annotations.values() if e['id'] in training]
        results = []
        for trial, samples, intervals in cases:
            observed = self.store.observations(trial['id'])
            # Keep observation timestamps distinct from reported measurement times.
            fine = [[r['at'], r['temperature'], r['humidity']] for r in observed if r.get('fresh')]
            connection = [{'series': 'connected', 'start': a['at'], 'end': b['at'],
                           'value': bool(a['connected'] and b['connected'])} for a, b in zip(observed, observed[1:])]
            a, b = trial['opened'] - 20 * MINUTE, trial['closed'] + 20 * MINUTE
            historic = detection(room, samples, intervals, a, b, labels=trained)['events']
            detailed = detection(room, fine, connection, a, b, labels=trained)['events']
            # Filter human labels from results: they are inputs to sensitivity, never predictions.
            match = lambda events: any(e['label'] is None and e['end'] >= trial['opened'] - 15 * MINUTE and e['start'] <= trial['closed'] for e in events)
            valid = [s for s in samples if finite(s[1])]
            coverage = bool(len(valid) >= 3 and valid[0][0] <= trial['opened'] and valid[-1][0] >= trial['closed']
                            and all(y[0] - x[0] <= 20 * MINUTE for x, y in zip(valid, valid[1:]))
                            and Context(intervals).connected(valid[0][0], valid[-1][0]))
            baseline = [e for e in historic if e['label'] is None and e['end'] < trial['opened'] and e['start'] >= trial['started']]
            results.append({'session': trial['id'], 'role': 'training' if 'guided:' + trial['id'] in training else 'holdout',
                            'confounded': bool(trial['confounded']), 'historySufficient': coverage,
                            'historyDetected': match(historic) if coverage else None,
                            'experimentDetected': match(detailed) if len(fine) >= 3 else None,
                            'baselineCandidates': len(baseline)})
        held = [r for r in results if r['role'] == 'holdout' and not r['confounded'] and r['historySufficient']]
        reviewed = [r for r in self.store.labels('real', room) if r['label'] in ('yes', 'no') and not r['id'].startswith('guided:')]
        return {'room': room, 'thresholdC': threshold, 'trials': results,
                'heldOutOpenings': len(held), 'heldOutDetected': sum(r['historyDetected'] for r in held),
                'reviewedNaturalEvents': len(reviewed),
                'precision': sum(r['label'] == 'yes' for r in reviewed) / len(reviewed) if reviewed else None,
                'note': 'Experimental results. Precision covers only reviewed natural events, not all detections.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history-root', type=Path, default=tado.ROOT)
    parser.add_argument('--web-root', type=Path, default=Path(os.environ.get('TADO_WEB_ROOT', Path.home() / 'Library/Application Support/TadoWeb')))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('start').add_argument('--room', type=int, required=True)
    commands.add_parser('report').add_argument('--room', type=int, required=True)
    for name in ('status', 'tick', 'run', 'mark'):
        command = commands.add_parser(name)
        command.add_argument('--session', required=True)
        if name == 'mark':
            command.add_argument('action', choices=['opened', 'closed', 'confounded', 'stop'])
    args = parser.parse_args()
    os.umask(0o077)
    c = Calibration(args.history_root, WindowStore(args.web_root))
    try:
        if args.command == 'report':
            result = c.report(args.room)
        elif args.command == 'start':
            result = c.start(args.room)
        elif args.command == 'mark':
            result = c.mark(args.session, args.action)
        elif args.command == 'run':
            previous = None
            while True:
                result = c.tick(args.session)
                encoded = json.dumps(result)
                if encoded != previous:
                    print(encoded, flush=True)
                    previous = encoded
                if result['status'] != 'active':
                    return
                time.sleep(5)
        else:
            result = getattr(c, args.command)(args.session) if args.command == 'tick' else c.state(args.session)
        print(json.dumps(result, indent=2))
    except KeyboardInterrupt:
        if getattr(args, 'session', None) and c.state(args.session)['status'] == 'active':
            c.mark(args.session, 'stop')
        parser.exit(130, 'Calibration stopped. Close the window if it is still open.\n')
    except Exception as exc:
        # Never echo provider bodies, credentials or private paths on unexpected failures.
        parser.exit(1, f'Calibration stopped ({type(exc).__name__}). Check local quota/configuration; close the window if needed.\n')


if __name__ == '__main__':
    main()
