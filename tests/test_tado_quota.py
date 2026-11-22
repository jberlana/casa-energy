"""Synthetic quota windows, migration, reset boundaries and shared admission."""
from datetime import datetime, timezone
import tempfile
from pathlib import Path
import unittest
from backend import tado_history as tado, tado_quota as quota


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = tado.connect(Path(self.temp.name), tado.DEFAULTS)
        self.now = datetime(2026, 11, 22, 15, 30, tzinfo=timezone.utc).timestamp()
        self.cfg = tado.DEFAULTS.copy()
        self.db.executemany('INSERT INTO rooms VALUES (1,?,?,1)', [(i, 'Synthetic') for i in range(1, 5)])
        self.db.commit()
    def tearDown(self):
        self.db.close(); self.temp.cleanup()
    def observe(self, limit=1000, remaining=954, seconds=None, status=200):
        headers = {'RateLimit-Policy': f'"perday";q={limit};w=86400', 'RateLimit': f'"perday";r={remaining}'}
        if seconds is not None: headers['RateLimit'] += f';t={seconds}'
        with self.db: quota.observe(self.db, headers, status, self.now)
    def budget(self, **kwargs):
        return quota.budget(self.db, self.cfg, self.now, **kwargs)

    def test_observed_policy_expands_budget_and_legacy_counts_are_not_lost(self):
        self.db.execute("INSERT INTO request_counts VALUES ('2026-11-22',46)"); self.db.commit()
        self.observe()
        result = self.budget(needed=7, scheduled=True)
        self.assertTrue(result['allowed'])
        self.assertEqual((result['limit'], result['budget'], result['used']), (1000, 800, 46))
        quota.reserve_request(self.db, self.cfg, self.now)
        self.assertEqual(self.budget()['used'], 47)
        self.assertEqual(self.budget()['remaining'], 953)

    def test_only_pending_slots_are_reserved_and_completed_slots_are_excluded(self):
        self.cfg['slots'] = ['06:05', '12:05', '18:05', '23:55']
        self.db.execute("INSERT INTO runs(day,slot,attempts,completed_at) VALUES ('2026-11-22','12:05',1,'done')")
        self.db.commit()
        self.assertEqual(self.budget(needed=7, scheduled=True)['scheduled'], 14)
        self.db.execute("INSERT INTO runs(day,slot,attempts,completed_at) VALUES ('2026-11-22','18:05',1,'done')")
        self.db.commit()
        self.assertEqual(self.budget(scheduled=True)['scheduled'], 7)

    def test_known_reset_reserves_next_day_slots_before_reset(self):
        self.observe(seconds=22*3600)
        result = self.budget(scheduled=True)
        # Current due slot, tonight's two slots, and tomorrow's morning/noon slots.
        self.assertEqual(result['scheduled'], 35)
        self.assertEqual(result['resetAt'], int((self.now+22*3600)*1000))

    def test_midnight_does_not_refill_local_allowance(self):
        self.now = datetime(2026, 11, 22, 23, 59, tzinfo=timezone.utc).timestamp()
        quota.write(self.db, 'quota_usage', [[int(self.now//60)*60, 80]]); self.db.commit()
        self.now += 120
        result = self.budget()
        self.assertEqual(result['reason'], 'local_budget')
        self.assertGreater(result['retryAt'], self.now*1000)
        with self.assertRaises(ValueError): quota.reserve_request(self.db, self.cfg, self.now)

    def test_auth_and_retries_reduce_observed_remaining_even_without_headers(self):
        self.observe(remaining=205)
        for _ in range(5): quota.reserve_request(self.db, self.cfg, self.now)
        self.assertEqual(self.budget()['reason'], 'safety_reserve')
        self.assertEqual(self.budget()['remaining'], 200)
        with self.assertRaises(ValueError): quota.reserve_request(self.db, self.cfg, self.now)

    def test_429_uses_server_reset_and_expiry_does_not_grant_full_quota(self):
        self.observe(remaining=0, seconds=1800, status=429)
        self.assertEqual(self.budget()['reason'], 'server_quota')
        self.assertEqual(self.budget()['retryAt'], (self.now+1800)*1000)
        self.now += 1801
        self.assertEqual(self.budget()['limit'], 100)
        self.assertTrue(self.budget()['allowed'])
        self.assertIsNone(self.budget()['remaining'])

    def test_429_retry_after_date_and_missing_deadline(self):
        with self.db: quota.observe(self.db, {'Retry-After': 'Sun, 22 Nov 2026 17:00:00 GMT'}, 429, self.now)
        self.assertEqual(self.budget()['retryAt'], datetime(2026,11,22,17,tzinfo=timezone.utc).timestamp()*1000)
        with self.db: quota.observe(self.db, {}, 429, self.now)
        self.assertEqual(self.budget()['retryAt'], (self.now+86400)*1000)

    def test_missing_malformed_or_stale_policy_never_increases_budget(self):
        for value in ['broken', '"perday";q=0;w=86400', '"perday";q=1000001;w=86400']:
            quota.write(self.db, 'server_quota', {'policy':value, 'remaining':1000, 'observed_at':datetime.fromtimestamp(self.now,timezone.utc).isoformat()})
            self.assertEqual(self.budget()['budget'], 80)
        self.observe(); self.now += 86401
        self.assertEqual(self.budget()['budget'], 80)
        with self.db: quota.observe(self.db, {'ratelimit':'"perday";r=990'}, 200, self.now)
        self.assertEqual(self.budget()['budget'], 80)

    def test_policy_decrease_and_explicit_fixed_cap_are_respected(self):
        self.observe(); self.assertEqual(self.budget()['budget'], 800)
        self.observe(limit=100, remaining=90)
        self.assertEqual(self.budget()['budget'], 80)
        self.observe(); self.cfg.update(adaptive_quota=False, daily_request_limit=40)
        self.assertEqual(self.budget()['budget'], 40)

    def test_reservation_and_hard_budget_have_distinct_reasons(self):
        self.db.execute("INSERT INTO request_counts VALUES ('2026-11-22',60)"); self.db.commit()
        self.assertEqual(self.budget(needed=7, scheduled=True)['reason'], 'scheduled_reserve')
        self.assertTrue(self.budget(needed=7)['allowed'])
        self.db.execute("UPDATE request_counts SET count=80"); self.db.commit()
        self.assertEqual(self.budget()['reason'], 'local_budget')

    def test_scheduled_reserve_uses_local_time_across_dst_without_resetting_usage(self):
        self.now = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc).timestamp()
        self.cfg['slots'] = ['06:05', '12:05']
        self.assertEqual(self.budget(scheduled=True)['scheduled'], 14)
        self.now += 3600
        self.assertEqual(self.budget(scheduled=True)['scheduled'], 14)

    def test_remaining_observation_does_not_extend_a_policy_without_confirmation(self):
        self.observe()
        self.now += 80000
        with self.db: quota.observe(self.db, {'ratelimit': '"perday";r=900'}, 200, self.now)
        self.assertTrue(self.budget()['adaptive'])
        self.now += 6401
        self.assertFalse(self.budget()['adaptive'])

    def test_authentication_burst_quota_does_not_replace_daily_allowance(self):
        self.observe()
        with self.db:
            quota.observe(self.db, {'ratelimit': '"perminute";r=2;t=30',
                'ratelimit-policy': '"perminute";q=10;w=60'}, 200, self.now)
        self.assertEqual(self.budget()['limit'], 1000)
        self.assertEqual(self.budget()['remaining'], 954)
        with self.db:
            quota.observe(self.db, {'ratelimit': '"perminute";r=2;t=30, "perday";r=900;t=500',
                'ratelimit-policy': '"perminute";q=10;w=60, "perday";q=1000;w=86400'}, 200, self.now)
        self.assertEqual(self.budget()['remaining'], 900)
        self.assertEqual(self.budget()['resetAt'], (self.now+500)*1000)
        with self.db: quota.observe(self.db, {'ratelimit': '"perminute";r=0;t=180'}, 429, self.now)
        self.assertEqual(self.budget()['retryAt'], (self.now+180)*1000)

    def test_manual_retries_cannot_spend_the_scheduled_reserve(self):
        self.cfg['reserve_scheduled'] = True
        self.db.execute("INSERT INTO request_counts VALUES ('2026-11-22',58)"); self.db.commit()
        quota.reserve_request(self.db, self.cfg, self.now)
        with self.assertRaisesRegex(ValueError, 'scheduled_reserve'):
            quota.reserve_request(self.db, self.cfg, self.now)
        self.cfg['reserve_scheduled'] = False
        quota.reserve_request(self.db, self.cfg, self.now)
        self.assertEqual(self.budget()['used'], 60)

    def test_read_only_budget_does_not_create_usage_state(self):
        self.budget()
        self.assertIsNone(quota.read(self.db, 'quota_usage'))
        self.assertIsNone(quota.read(self.db, 'quota_sequence'))


if __name__ == '__main__': unittest.main()
