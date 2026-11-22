"""Synthetic ventilation, annotation and bounded calibration contracts."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from backend import tado_history as tado, web_server as web
from backend.window_calibration import Calibration, LIMIT, safe_reading
from backend.window_detection import DAY, MINUTE, VERSION, absolute_humidity, detection, guided_annotations, room_threshold
from backend.window_store import WindowStore

BASE = 1795000000000 // DAY * DAY


def samples(values=(21, 21, 19.9, 19.8, 20), base=BASE, step=15 * MINUTE):
    return [[base + i * step, value, 50] for i, value in enumerate(values)]


def events(base=BASE, duration=2 * DAY):
    return [{'series': 'connected', 'start': base, 'end': base + duration, 'value': True},
            {'series': 'callForHeat', 'start': base, 'end': base + duration, 'value': 'MEDIUM'}]


def detect(rows=None, states=None, **kwargs):
    return detection(1, rows if rows is not None else samples(), states if states is not None else events(),
                     BASE, BASE + 2 * DAY, **kwargs)


class DetectionTests(unittest.TestCase):
    def test_sustained_drop_has_uncertain_onset_and_no_claim_of_closure(self):
        result = detect()['events']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['onset'], [BASE + 15 * MINUTE, BASE + 30 * MINUTE])
        self.assertEqual(result[0]['dropC'], 1.1)
        self.assertTrue(result[0]['provisional'])
        self.assertNotIn('closed', result[0])

    def test_slow_cooling_humidity_change_and_single_spike_are_not_openings(self):
        for values in [(21, 20.9, 20.8, 20.7, 20.6), (21, 21, 19, 21, 21), (21, 21, 21, 21, 21)]:
            rows = samples(values)
            rows[2][2] = 20
            self.assertEqual(detect(rows)['events'], [])

    def test_one_long_cooling_episode_does_not_create_repeated_markers(self):
        result = detect(samples(tuple(24 - i * .8 for i in range(10))))['events']
        self.assertEqual(len(result), 1)

    def test_missing_values_gaps_and_disconnections_are_not_bridged(self):
        self.assertEqual(detect(samples((21, None, 19, 19, 19)))['events'], [])
        self.assertEqual(detect(samples((21, 19, 19), step=25 * MINUTE))['events'], [])
        self.assertEqual(detect(states=[])['events'], [])
        offline = [{'series': 'connected', 'start': BASE, 'end': BASE + DAY, 'value': False}]
        self.assertEqual(detect(states=offline)['events'], [])

    def test_heating_stop_and_target_change_are_explained(self):
        state = events()
        state[1]['end'] = BASE + 15 * MINUTE
        state.extend([{'series': 'callForHeat', 'start': BASE + 15 * MINUTE, 'end': BASE + DAY, 'value': 'NONE'},
                      {'series': 'settings', 'start': BASE + 15 * MINUTE, 'end': BASE + DAY, 'value': {'power': 'OFF'}}])
        reasons = detect(states=state)['events'][0]['alternatives']
        self.assertIn('La demanda acababa de terminar', reasons)
        self.assertIn('Coincide con un cambio de consigna', reasons)

    def test_cross_room_cooling_and_outside_context(self):
        result = detect(peers=[samples()], outdoor=[[BASE, 22]])['events'][0]
        self.assertIn('Otras habitaciones se enfrían al mismo tiempo', result['alternatives'])
        self.assertIn('Poca diferencia entre temperatura interior y exterior', result['alternatives'])
        self.assertIsNotNone(result['absoluteHumidityChange'])

    def test_null_tado_is_unknown_and_activated_false_is_not_no_detection(self):
        self.assertEqual(detect(rows=[], snapshots=[{'at': BASE, 'openWindow': None}])['events'], [])
        self.assertEqual(detect(rows=[], snapshots=[{'at': BASE, 'openWindow': {'unknownShape': True}}])['events'], [])
        result = detect(snapshots=[{'at': BASE + 30 * MINUTE, 'openWindow': {'activated': False}}])['events']
        self.assertEqual(len(result), 1)
        self.assertIn('tado', result[0]['origins'])
        self.assertFalse(result[0]['tadoShutoff'])

    def test_repeated_tado_observations_merge_without_inventing_expiry(self):
        result = detect(rows=[], snapshots=[{'at': BASE + i * MINUTE, 'openWindow': {'activated': True, 'expiry': 'unknown'}} for i in [0, 2, 4]])['events']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['end'], BASE)

    def test_annotation_survives_recalculation_and_questions_stop_after_three(self):
        event = detect()['events'][0]
        labels = [{'id': f'event:{i}', 'label': label, 'created': BASE + i, 'event': {**event, 'id': f'event:{i}', 'start': BASE + i * DAY, 'end': BASE + i * DAY + MINUTE}} for i, label in enumerate(['yes', 'no', 'unknown'])]
        result = detect(rows=[], labels=labels)
        self.assertFalse(result['askForFeedback'])
        self.assertEqual(result['reviewedCount'], 3)
        self.assertEqual(result['events'][0]['label'], 'yes')

    def test_training_uses_distinct_days_and_holds_out_later_trials(self):
        event = detect()['events'][0]
        labels = [{'id': str(i), 'label': 'yes', 'created': BASE + i * DAY,
                   'event': {**event, 'start': BASE + i * DAY, 'dropC': drop}} for i, drop in enumerate([.6, .8, 3])]
        threshold, train = room_threshold(labels)
        self.assertAlmostEqual(threshold, .49)
        self.assertEqual(train, ['0', '1'])
        labels[1]['event']['start'] = BASE
        self.assertEqual(room_threshold(labels[:2])[1], [])

    def test_absolute_humidity_and_nonfinite_inputs(self):
        self.assertAlmostEqual(absolute_humidity(20, 50), 8.62, places=1)
        self.assertIsNone(absolute_humidity(float('nan'), 50))
        self.assertEqual(detect(samples((21, float('nan'), 19, 19, 19)))['events'], [])

    def test_baseline_requires_thirty_comparable_intervals(self):
        baseline = [[BASE + i * 15 * MINUTE, 22 - i * .01, 50] for i in range(40)]
        baseline += [[BASE + 40 * 15 * MINUTE, 20, 50], [BASE + 41 * 15 * MINUTE, 20, 50]]
        result = detect(baseline)['events'][0]
        self.assertGreaterEqual(result['referenceCount'], 30)
        self.assertIn('Más rápido que el enfriamiento habitual', result['reasons'])


class WindowAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = WindowStore(self.root / 'web')
        self.db = tado.connect(self.root, tado.DEFAULTS)
        self.db.execute("INSERT INTO rooms VALUES (1,1,'Synthetic',1)")
        for t, value, humidity in samples():
            self.db.execute('INSERT INTO samples VALUES (1,1,?,?,?)', (web.iso(t), value, humidity))
        for e in events():
            self.db.execute('INSERT INTO events VALUES (1,1,?,?,?,?,?)', ('2026-11-18', e['series'], web.iso(e['start']), web.iso(e['end']), json.dumps(e['value'])))
        self.db.commit()
        self.patches = [patch.object(web, 'ROOT', self.root), patch.object(web, 'window_store', self.store),
                        patch.object(web, 'read_environment', return_value={'weather': []})]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in self.patches: p.stop()
        self.db.close()
        self.temp.cleanup()

    def read(self, start=BASE, end=BASE + DAY):
        return web.window_events(1, start, end)

    def request(self, origin='http://localhost', flag='1'):
        return Request({'type': 'http', 'headers': [(b'host', b'localhost'), (b'origin', origin.encode()), (b'x-casa-feedback', flag.encode())]})

    def body(self, event, label='yes', source='real'):
        return web.WindowFeedback(source=source, room=1, eventId=event['id'], start=event['start'], end=event['end'], label=label)

    def test_read_does_not_create_annotation_store_or_modify_history(self):
        before = (self.root / 'history.sqlite3').read_bytes()
        self.assertEqual(len(self.read()['events']), 1)
        self.assertFalse(self.store.path.exists())
        self.assertEqual(before, (self.root / 'history.sqlite3').read_bytes())

    def test_identity_is_stable_across_viewports(self):
        a = self.read()['events'][0]
        b = self.read(BASE + 5 * MINUTE, BASE + 40 * MINUTE)['events'][0]
        self.assertEqual(a, b)

    def test_feedback_is_persistent_source_isolated_and_correctable(self):
        event = self.read()['events'][0]
        before = (self.root / 'history.sqlite3').read_bytes()
        web.window_feedback(self.request(), self.body(event))
        self.assertEqual(WindowStore(self.root / 'web').labels('real', 1)[0]['label'], 'yes')
        self.assertEqual(self.store.labels('demo', 1), [])
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        web.window_feedback(self.request(), self.body(event, 'no'))
        result = self.read()
        self.assertEqual(result['reviewedCount'], 1)
        self.assertEqual(result['events'][0]['label'], 'no')
        self.assertEqual(before, (self.root / 'history.sqlite3').read_bytes())

    def test_cross_origin_missing_header_and_invented_events_are_rejected(self):
        body = self.body(self.read()['events'][0])
        for req in [self.request('https://other.invalid'), self.request(flag='')]:
            with self.assertRaises(HTTPException) as raised: web.window_feedback(req, body)
            self.assertEqual(raised.exception.status_code, 403)
        body.eventId = 'invented'
        with self.assertRaises(HTTPException) as raised: web.window_feedback(self.request(), body)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertFalse(self.store.path.exists())

    def test_feedback_store_full_returns_error_and_preserves_prior_labels(self):
        event = self.read()['events'][0]
        web.window_feedback(self.request(), self.body(event))
        with patch.object(self.store, 'save_label', side_effect=sqlite3.OperationalError('full')):
            with self.assertRaises(HTTPException) as raised: web.window_feedback(self.request(), self.body(event, 'no'))
            self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.store.labels('real', 1)[0]['label'], 'yes')

    def test_optional_weather_failure_does_not_hide_events(self):
        with patch.object(web, 'read_environment', side_effect=sqlite3.OperationalError):
            self.assertEqual(len(self.read()['events']), 1)

    def test_demo_produces_candidates_without_accessing_history(self):
        with patch.object(web, 'database', side_effect=AssertionError('private access')):
            result = web.window_events(1, web.DEMO_END - 7 * DAY, web.DEMO_END, 'demo')
        self.assertTrue(result['events'])


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.history = self.root / 'history'
        self.history.mkdir()
        self.now = BASE
        self.store = WindowStore(self.root / 'web')
        self.cfg = tado.DEFAULTS | {'home_id': 1, 'client_id': 'synthetic', 'auth_path': str(self.root / 'auth.json')}
        (self.history / 'config.json').write_text(json.dumps(self.cfg))
        (self.root / 'auth.json').write_text(json.dumps({'access_token': 'synthetic', 'expires_in': 99999999, 'obtained_at': BASE / 1000}))
        self.db = tado.connect(self.history, self.cfg)
        self.db.execute("INSERT INTO rooms VALUES (1,1,'Synthetic',1)")
        tado.state_set(self.db, 'server_quota', {'remaining': 1000, 'observed_at': web.iso(self.now)})
        self.db.commit()
        self.c = Calibration(self.history, self.store, clock=lambda: self.now)
        self.clock_patch = patch.object(tado, 'utcnow', side_effect=lambda: datetime.fromtimestamp(self.now / 1000, timezone.utc))
        self.clock_patch.start()

    def tearDown(self):
        self.clock_patch.stop()
        self.db.close()
        self.temp.cleanup()

    def api(self, api, host, path):
        self.assertEqual(host, 'hops.tado.com')
        self.assertEqual(path, '/homes/1/rooms')
        api.reserve_request()
        return [{'id': 1, 'connection': {'state': 'CONNECTED'},
                 'sensorDataPoints': {'insideTemperature': {'value': 21, 'timestamp': web.iso(self.now)}, 'humidity': {'percentage': 50}}}]

    @contextmanager
    def network(self, handler=None):
        with patch.object(tado.API, 'get', autospec=True, side_effect=handler or self.api) as call:
            yield call

    def baseline(self, session):
        with self.network():
            for _ in range(6):
                self.c.tick(session)
                self.now += 2 * MINUTE

    def test_start_has_no_network_and_requires_quota_and_single_session(self):
        with self.network() as network:
            first = self.c.start(1)
            network.assert_not_called()
            with self.assertRaises(ValueError): self.c.start(1)
        self.assertFalse(first['canOpen'])

    def test_baseline_gates_open_and_reports_actual_marks(self):
        session = self.c.start(1)['id']
        with self.assertRaises(ValueError): self.c.mark(session, 'opened')
        self.baseline(session)
        self.assertTrue(self.c.state(session)['canOpen'])
        self.c.mark(session, 'opened')
        self.now += 5 * MINUTE
        result = self.c.mark(session, 'closed')
        self.assertEqual(result['closed'] - result['opened'], 5 * MINUTE)

    def test_polling_is_two_minutes_and_restart_cannot_extend_expiry(self):
        session = self.c.start(1)['id']
        with self.network() as network:
            self.c.tick(session); self.c.tick(session)
            self.assertEqual(network.call_count, 1)
            self.now += 31 * MINUTE
            restarted = Calibration(self.history, self.store, clock=lambda: self.now)
            result = restarted.tick(session)
            self.assertEqual(result['status'], 'expired')
            self.assertEqual(network.call_count, 1)

    def test_stale_unchanged_snapshots_never_authorize_opening(self):
        def stale(api, host, path):
            rows = self.api(api, host, path)
            rows[0]['sensorDataPoints']['insideTemperature'].pop('timestamp')
            return rows
        session = self.c.start(1)['id']
        with self.network(stale):
            for _ in range(8):
                self.c.tick(session)
                self.now += 2 * MINUTE
        self.assertFalse(self.c.state(session)['canOpen'])
        self.assertEqual(self.c.state(session)['status'], 'insufficient')

    def test_retry_and_auth_attempts_share_twenty_request_cap(self):
        session = self.c.start(1)['id']
        def excessive(api, host, path):
            for _ in range(LIMIT + 1): api.reserve_request()
            return []
        with self.network(excessive):
            with self.assertRaises(ValueError): self.c.tick(session)
        self.assertEqual(self.c.state(session)['requests'], LIMIT)
        with self.network() as network:
            self.c.tick(session)
            network.assert_not_called()

    def test_quota_failure_preserves_scheduled_reserve(self):
        tado.state_set(self.db, 'server_quota', {'remaining': 20, 'observed_at': web.iso(self.now)})
        with self.network() as network:
            with self.assertRaises(ValueError): self.c.start(1)
            network.assert_not_called()

    def test_stop_is_persistent_and_no_future_reads(self):
        session = self.c.start(1)['id']
        self.c.mark(session, 'stop')
        with self.network() as network:
            self.c.tick(session)
            network.assert_not_called()

    def test_foreign_fields_are_not_saved_and_missing_timestamps_stay_unknown(self):
        reading = safe_reading({'secret': 'synthetic', 'sensorDataPoints': {'insideTemperature': {'value': 20}}}, BASE)
        self.assertNotIn('secret', reading)
        self.assertIsNone(reading['measuredAt'])

    def test_guided_learning_uses_regular_history_not_experimental_resolution(self):
        trial = {'id': 'test', 'opened': BASE + 10 * MINUTE, 'closed': BASE + 15 * MINUTE, 'confounded': 0}
        annotation = guided_annotations([trial], samples((21, 20.4, 21)), events())
        self.assertEqual(annotation[0]['event']['dropC'], .6)
        self.assertIsNone(guided_annotations([trial], samples((21, 20.4, 21)))[0]['event']['dropC'])
        trial['confounded'] = 1
        self.assertIsNone(guided_annotations([trial], samples())[0]['event']['dropC'])

    def test_report_separates_training_from_holdout_and_never_counts_labels_as_predictions(self):
        for i in range(3):
            base = BASE + i * DAY
            with self.store.connect(True) as state:
                state.execute('INSERT INTO experiments(id,room,started,expires,status,next_poll,opened,closed,fresh) VALUES (?,?,?,?,?,?,?,?,?)',
                              (str(i), 1, base + 6 * MINUTE, base + 36 * MINUTE, 'expired', base, base + 16 * MINUTE, base + 22 * MINUTE, 1))
            for t, value, humidity in samples(base=base):
                self.db.execute('INSERT INTO samples VALUES (1,1,?,?,?)', (web.iso(t), value, humidity))
            for event in events(base, duration=75 * MINUTE):
                self.db.execute('INSERT INTO events VALUES (1,1,?,?,?,?,?)',
                                (web.iso(base)[:10], event['series'], web.iso(event['start']), web.iso(event['end']), json.dumps(event['value'])))
        self.db.commit()
        result = self.c.report(1)
        self.assertEqual([t['role'] for t in result['trials']], ['training', 'training', 'holdout'])
        self.assertEqual(result['heldOutOpenings'], 1)
        self.assertEqual(result['heldOutDetected'], 1)
        self.assertIsNone(result['precision'])

    def test_recent_requests_do_not_make_stale_measurements_fresh(self):
        session = self.c.start(1)['id']
        self.baseline(session)
        def stale(api, host, path):
            rows = self.api(api, host, path)
            rows[0]['sensorDataPoints']['insideTemperature']['timestamp'] = web.iso(BASE)
            rows[0]['sensorDataPoints']['insideTemperature']['value'] = 19
            return rows
        self.now = BASE + 14 * MINUTE
        with self.network(stale): self.c.tick(session)
        self.assertFalse(self.c.state(session)['canOpen'])


if __name__ == '__main__':
    unittest.main()
