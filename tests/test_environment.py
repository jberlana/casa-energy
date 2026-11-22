from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import environment_history as env
import web_server as web


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = env.connect(self.root)
        self.base = int(datetime(2026, 10, 25, tzinfo=timezone.utc).timestamp()*1000)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def payload(self):
        return {
            "hourly": {"time": [(self.base+i*env.HOUR)//1000 for i in range(3)],
                       "temperature_2m": [10, 11, 12], "precipitation": [0, .5, 1],
                       "weather_code": [0, 61, 61], "cloud_cover": [0, 80, 90], "is_day": [0, 0, 0]},
            "daily": {"time": [(self.base-2*env.HOUR)//1000],
                      "sunrise": [(self.base+6*env.HOUR)//1000], "sunset": [(self.base+17*env.HOUR)//1000]},
        }

    def test_future_weather_is_not_history_and_dst_day_has_25_hours(self):
        env.store_weather(self.db, self.payload(), "test", self.base+env.HOUR)
        result = env.read_environment(self.base-3*env.HOUR, self.base+3*env.HOUR, self.root, self.base+env.HOUR)
        self.assertEqual(len(result["weather"]), 2)
        self.assertEqual(result["days"][0]["end"]-result["days"][0]["start"], 25*env.HOUR)
        self.assertEqual(result["weather"][-1]["timestamp"], self.base+env.HOUR)

    def test_repeated_weather_is_idempotent_and_null_does_not_erase_known_values(self):
        data = self.payload()
        env.store_weather(self.db, data, "test", self.base+3*env.HOUR)
        data["hourly"]["temperature_2m"][0] = None
        env.store_weather(self.db, data, "test", self.base+3*env.HOUR)
        self.assertEqual(self.db.execute("SELECT count(*) FROM weather").fetchone()[0], 3)
        self.assertEqual(self.db.execute("SELECT temperature FROM weather ORDER BY timestamp LIMIT 1").fetchone()[0], 10)

    def test_hue_observation_time_is_not_backdated_to_last_change(self):
        resource = {"enabled": True, "temperature": {"temperature": 16.5, "temperature_valid": True,
                    "temperature_report": {"changed": "2026-10-24T10:00:00Z"}}}
        with self.db:
            env.store_outdoor(self.db, resource, True, self.base)
            env.store_outdoor(self.db, resource, False, self.base+5*60_000)
        result = env.read_environment(self.base-env.DAY, self.base+env.HOUR, self.root, self.base+env.HOUR)
        self.assertEqual(result["firstOutdoor"], self.base)
        self.assertEqual(result["outdoor"][0][:2], [self.base, 16.5])
        self.assertLess(result["outdoor"][0][2], self.base)
        self.assertIsNone(result["outdoor"][1][1])
        self.assertFalse(result["hueStatus"]["ok"])

    def test_downsample_keeps_extrema_and_disconnects_acquisition_gaps(self):
        with self.db:
            for i, temp in [(0, 10), (1, -2), (2, 40), (3, 11), (60, 12)]:
                self.db.execute("INSERT INTO outdoor VALUES (?,?,?,?,1)", (self.base+i*60_000, temp, self.base, "Entrada"))
        result = env.read_environment(self.base, self.base+30*env.DAY, self.root, self.base+30*env.DAY)
        values = [s[1] for s in result["outdoor"] if s[1] is not None]
        self.assertEqual(min(values), -2)
        self.assertEqual(max(values), 40)
        self.assertTrue(any(s[1] is None for s in result["outdoor"]))

    def test_reads_do_not_mutate_database_or_call_external_services(self):
        env.store_weather(self.db, self.payload(), "test", self.base+env.HOUR)
        before = (self.root / "environment.sqlite3").read_bytes()
        with patch.object(env, "read_json", side_effect=AssertionError("network")):
            env.read_environment(self.base, self.base+env.HOUR, self.root, self.base+env.HOUR)
        self.assertEqual(before, (self.root / "environment.sqlite3").read_bytes())

    def test_weather_failure_is_rate_limited(self):
        with patch.object(env, "fetch_weather", side_effect=OSError("offline")) as request:
            with self.assertRaises(OSError): env.collect_weather(self.db, self.base)
            env.collect_weather(self.db, self.base+5*60_000)
            request.assert_called_once()

    def test_demo_never_contains_real_sensor_or_meteorological_data(self):
        with patch.object(web, "read_environment", side_effect=AssertionError("real data")):
            result = web.environment(self.base, self.base+env.DAY, "demo")
        self.assertEqual(result["outdoor"], [])
        self.assertEqual(result["weather"], [])


if __name__ == "__main__":
    unittest.main()
