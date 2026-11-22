import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import tado_history as collector
import web_server as web
from fastapi import HTTPException


class HistoryWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = collector.connect(self.root, collector.DEFAULTS)
        self.db.execute("INSERT INTO rooms VALUES (1,1,'Test',1)")
        self.base = web.ms('2026-10-25T00:00:00Z')  # DST fallback day, all calculations remain UTC.
        self.patch = patch.object(web, 'ROOT', self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.db.close()
        self.temp.cleanup()

    def event(self, series, start, end, value, day='2026-10-25'):
        self.db.execute('INSERT INTO events VALUES (1,1,?,?,?,?,?)',
                        (day, series, web.iso(self.base+start), web.iso(self.base+end), json.dumps(value)))

    def sample(self, offset, temperature, humidity=50):
        self.db.execute('INSERT INTO samples VALUES (1,1,?,?,?)',
                        (web.iso(self.base+offset), temperature, humidity))

    def test_read_only_connection_rejects_writes_and_preserves_database(self):
        self.db.commit()
        before = (self.root/'history.sqlite3').read_bytes()
        with self.assertRaises(HTTPException):
            with web.database() as db:
                db.execute('DELETE FROM rooms')
        self.assertEqual(before, (self.root/'history.sqlite3').read_bytes())

    def test_summary_clips_intervals_distinguishes_none_unknown_and_target(self):
        minute = web.MINUTE
        self.event('settings',0,60*minute,{'power':'ON','temperature':{'celsius':24}})
        self.event('callForHeat',0,20*minute,'NONE')
        self.event('callForHeat',20*minute,40*minute,'MEDIUM')
        self.event('callForHeat',40*minute,60*minute,'UNRECOGNIZED')
        self.sample(15*minute,20); self.sample(45*minute,21)
        self.db.commit()
        data=web.summary(1,self.base+10*minute,self.base+50*minute)
        self.assertEqual(data['heatMs'],20*minute)
        self.assertEqual(data['knownMs'],30*minute)
        self.assertEqual(data['change'],1)
        self.assertEqual(data['overshoot'],0)

    def test_overlapping_reports_do_not_double_count_heat(self):
        self.event('callForHeat',0,60*web.MINUTE,'MEDIUM','2026-10-24')
        self.event('callForHeat',30*web.MINUTE,90*web.MINUTE,'NONE')
        self.db.commit()
        data=web.summary(1,self.base,self.base+90*web.MINUTE)
        self.assertEqual(data['heatMs'],30*web.MINUTE)
        self.assertEqual(data['knownMs'],90*web.MINUTE)

    def test_overview_preserves_temperature_humidity_peaks_and_gaps(self):
        for offset,temp,humidity in [(0,20,50),(1,35,50),(2,5,50),(3,20,95),(4,20,10),(50,21,55)]:
            self.sample(offset*web.MINUTE,temp,humidity)
        self.db.commit()
        result=web.series(1,self.base,self.base+web.DAY,buckets=100)
        self.assertEqual(min(s[1] for s in result['samples']),5)
        self.assertEqual(max(s[1] for s in result['samples']),35)
        self.assertEqual(max(s[2] for s in result['samples']),95)
        self.assertEqual(min(s[2] for s in result['samples']),10)
        self.assertEqual(result['gaps'],[[self.base+4*web.MINUTE,self.base+50*web.MINUTE]])

    def test_export_keeps_original_samples_and_labels_demo(self):
        for i in range(30): self.sample(i*web.MINUTE,20+i/10)
        self.db.commit()
        response=web.export(1,self.base,self.base+30*web.MINUTE)
        rows=list(csv.DictReader(io.StringIO(response.body.decode())))
        self.assertEqual(len(rows),30)
        self.assertEqual(rows[0]['source'],'real')
        demo=web.export(101,web.DEMO_END-2*web.MINUTE,web.DEMO_END,'demo')
        self.assertIn('demo,101,',demo.body.decode())

    def test_demo_is_deterministic_and_does_not_write_history(self):
        self.db.commit()
        before=(self.root/'history.sqlite3').read_bytes()
        start=web.DEMO_END-60*web.MINUTE
        a=web.get_data('demo',101,start,web.DEMO_END)[0]
        b=web.get_data('demo',101,start+30*web.MINUTE,web.DEMO_END)[0]
        self.assertEqual(a[-len(b):],b)
        self.assertEqual(before,(self.root/'history.sqlite3').read_bytes())
        self.assertEqual(web.metadata('demo')['rooms'][0]['temperature'],a[-1][1])

    def test_invalid_ranges_sources_and_rooms_are_rejected(self):
        for start,end in [(2,1),(-1,50),(0,367*web.DAY)]:
            with self.assertRaises(HTTPException): web.window(start,end)
        with self.assertRaises(HTTPException): web.get_data('not-real',1,0,50)
        with self.assertRaises(HTTPException): web.get_data('real',999,0,50)


if __name__ == '__main__':
    unittest.main()
