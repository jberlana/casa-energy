import copy
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend import tado_history as t


def report(end="2026-11-22T10:00:00Z"):
    interval = {"from": "2026-11-21T23:00:00Z", "to": end}
    return {
        "interval": interval,
        "measuredData": {
            "insideTemperature": {"dataPoints": [{"timestamp": "2026-11-22T06:00:00Z", "value": {"celsius": 21.5}}]},
            "humidity": {"dataPoints": [{"timestamp": "2026-11-22T06:00:00Z", "value": 0.56}]},
            "measuringDeviceConnected": {"dataIntervals": [interval | {"value": True}]},
        },
        "settings": {"dataIntervals": [interval | {"value": {"power": "ON", "temperature": {"celsius": 22}}}]},
        "stripes": {"dataIntervals": [interval | {"value": {"stripeType": "HOME"}}]},
        "callForHeat": {"dataIntervals": [interval | {"value": "NONE"}]},
    }


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = t.DEFAULTS | {"minimum_free_bytes": 0}
        self.db = t.connect(self.root, self.config)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def save(self, data, slot="06:05"):
        t.persist_report(self.db, 1, 2, "2026-11-22", slot, data)

    def test_repeated_report_does_not_duplicate_and_extends_intervals(self):
        self.save(report())
        self.save(report("2026-11-22T12:00:00Z"), "12:05")
        self.save(report("2026-11-22T12:00:00Z"), "12:05")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 4)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT humidity_pct FROM samples").fetchone()[0], 56.00000000000001)
        self.assertTrue(self.db.execute("SELECT coverage_end FROM reports").fetchone()[0].startswith("2026-11-22T12:00"))

    def test_shorter_or_missing_series_does_not_replace_history(self):
        self.save(report())
        with self.assertRaises(t.CollectorError):
            self.save(report("2026-11-22T09:00:00Z"))
        broken = report("2026-11-22T11:00:00Z")
        broken["measuredData"]["humidity"]["dataPoints"] = []
        with self.assertRaises(t.CollectorError):
            self.save(broken)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)

    def test_corrected_intervals_replace_old_boundary(self):
        self.save(report())
        updated = report("2026-11-22T12:00:00Z")
        updated["callForHeat"]["dataIntervals"] = [
            {"from": "2026-11-21T23:00:00Z", "to": "2026-11-22T07:00:00Z", "value": "NONE"},
            {"from": "2026-11-22T07:00:00Z", "to": "2026-11-22T12:00:00Z", "value": "HEATING"},
        ]
        self.save(updated)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE series='callForHeat'").fetchone()[0], 2)

    def test_moving_terminal_observation_is_retained_when_new_report_advances(self):
        old=report()
        for series,value in [('insideTemperature',{'celsius':22}),('humidity',.58)]:
            old['measuredData'][series]['dataPoints'].append({'timestamp':old['interval']['to'],'value':value})
        self.save(old)
        new=report('2026-11-22T12:00:00Z')
        for series,value in [('insideTemperature',{'celsius':23}),('humidity',.59)]:
            new['measuredData'][series]['dataPoints'].append({'timestamp':new['interval']['to'],'value':value})
        self.save(new)
        self.assertEqual(self.db.execute('SELECT count(*) FROM samples').fetchone()[0],3)
        self.assertEqual(self.db.execute("SELECT temperature_c FROM samples WHERE timestamp LIKE '%T10:00:%'").fetchone()[0],22)
        self.assertIn('T12:00:',self.db.execute('SELECT coverage_end FROM reports').fetchone()[0])

    def test_missing_terminal_field_without_newer_measurement_is_rejected(self):
        old=report()
        old['measuredData']['humidity']['dataPoints'].append({'timestamp':old['interval']['to'],'value':.58})
        self.save(old)
        with self.assertRaises(t.CollectorError):
            self.save(report('2026-11-22T12:00:00Z'))

    def test_setting_on_is_not_converted_to_heating(self):
        self.save(report())
        row = self.db.execute("SELECT value_json FROM events WHERE series='callForHeat'").fetchone()
        self.assertEqual(json.loads(row[0]), "NONE")

    def test_report_cannot_remove_middle_of_known_heat_interval(self):
        self.save(report())
        broken = report("2026-11-22T12:00:00Z")
        broken["callForHeat"]["dataIntervals"] = [
            {"from": "2026-11-21T23:00:00Z", "to": "2026-11-22T07:00:00Z", "value": "NONE"},
            {"from": "2026-11-22T08:00:00Z", "to": "2026-11-22T12:00:00Z", "value": "HEATING"},
        ]
        with self.assertRaises(t.CollectorError):
            self.save(broken)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE series='callForHeat'").fetchone()[0], 1)

    def test_transaction_rolls_back_when_database_rejects_event(self):
        self.save(report())
        before = bytes(self.db.execute("SELECT raw_gzip FROM reports").fetchone()[0])
        self.db.execute("CREATE TRIGGER reject_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.Error):
            self.save(report("2026-11-22T12:00:00Z"))
        self.assertEqual(bytes(self.db.execute("SELECT raw_gzip FROM reports").fetchone()[0]), before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 4)

    def test_local_quota_survives_new_client(self):
        auth = self.root / "auth.json"
        t.atomic_json(auth, {"access_token": "test"})
        config = self.config | {"auth_path": str(auth), "daily_request_limit": 2}
        t.API(self.db, config).reserve_request()
        t.API(self.db, config).reserve_request()
        with self.assertRaises(t.CollectorError):
            t.API(self.db, config).reserve_request()

    def test_server_quota_pause_is_persistent(self):
        auth = self.root / "auth.json"
        t.atomic_json(auth, {"access_token": "test"})
        config = self.config | {"auth_path": str(auth)}
        api = t.API(self.db, config)
        api.headers({"ratelimit": '"perday";r=0;t=3600'}, 429)
        with self.assertRaises(t.CollectorError):
            t.API(self.db, config).reserve_request()

    def test_disk_budget_pauses_without_deleting_history(self):
        self.save(report())
        with self.assertRaises(t.CollectorError):
            t.disk_guard(self.root, self.config | {"storage_limit_bytes": 1})
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)
        health = t.status(self.db, self.root, self.config | {"storage_limit_bytes": 1})
        self.assertIn("storage budget", health["storage_warning"])

    def test_wake_only_collects_latest_slot_and_handles_dst(self):
        now = datetime(2026, 11, 22, 18, 30, tzinfo=timezone.utc)
        self.assertEqual(t.due_slot(self.db, self.config, now), ("2026-11-22", "18:05"))
        t.state_set(self.db, "last_success", t.stamp(now))
        self.assertIsNone(t.due_slot(self.db, self.config, datetime(2026, 11, 22, 0, 30, tzinfo=timezone.utc)))
        summer = datetime(2026, 7, 1, 4, 6, tzinfo=timezone.utc)
        self.assertEqual(t.due_slot(self.db, self.config, summer), ("2026-07-01", "06:05"))

    def test_completed_slots_do_not_make_more_requests(self):
        now = datetime(2026, 11, 22, 12, 30, tzinfo=timezone.utc)
        self.db.execute("INSERT INTO runs(day,slot,completed_at) VALUES (?,?,?)", ("2026-11-22", "12:05", t.stamp(now)))
        self.db.commit()
        self.assertIsNone(t.due_slot(self.db, self.config, now))

    def test_backup_is_readable(self):
        self.save(report())
        t.backup(self.db, self.root, self.config, "2026-11-22")
        with sqlite3.connect(self.root / "backup.sqlite3") as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reports").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
