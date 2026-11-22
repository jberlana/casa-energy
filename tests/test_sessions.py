import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.heating_sessions import analyse, MINUTE, DAY
from backend import web_server as web


def event(a,b,value,series='callForHeat'):
    return {'start':a*MINUTE,'end':b*MINUTE,'series':series,'value':value}


def room(events,samples=None):
    return {'name':'Room','events':events,'samples':samples or []}


class SessionTests(unittest.TestCase):
    def analyse(self, rooms, start=0, end=60, source='real'):
        return analyse(rooms,1,start*MINUTE,end*MINUTE,0,90*MINUTE,source)

    def test_union_overlapping_rooms_counts_once_and_tracks_room_durations(self):
        rooms={1:room([event(0,10,'NONE'),event(10,30,'LOW'),event(30,90,'NONE')]),
               2:room([event(0,20,'NONE'),event(20,40,'HIGH'),event(40,90,'NONE')])}
        result=self.analyse(rooms)
        s=result['sessions'][0]
        self.assertEqual(len(result['sessions']),1)
        self.assertEqual(s['durationMs'],30*MINUTE)
        self.assertEqual([r['demandMs'] for r in s['rooms']],[20*MINUTE,20*MINUTE])
        self.assertFalse(s['partialStart']); self.assertFalse(s['partialEnd'])
        self.assertIsNone(s['cost'])
        self.assertEqual(result['days'][0]['demandMs'],30*MINUTE)

    def test_unknown_gap_and_disconnect_split_sessions_without_implying_off(self):
        r=room([event(0,90,'HIGH'),event(20,30,False,'connected')])
        data=self.analyse({1:r})
        self.assertEqual(len(data['sessions']),2)
        self.assertTrue(all(s['partialStart'] and s['partialEnd'] for s in data['sessions']))
        self.assertEqual(data['days'][0]['knownMs'],50*MINUTE)

    def test_inactive_room_with_missing_data_cannot_confirm_end(self):
        data=self.analyse({1:room([event(0,10,'NONE'),event(10,20,'HIGH'),event(20,90,'NONE')]),2:room([])})
        self.assertTrue(data['sessions'][0]['partialStart'])
        self.assertTrue(data['sessions'][0]['partialEnd'])
        self.assertEqual(data['days'][0]['knownMs'],10*MINUTE)

    def test_visible_duration_clipped_but_session_keeps_context(self):
        data=self.analyse({1:room([event(0,5,'NONE'),event(5,70,'HIGH'),event(70,90,'NONE')])},20,30)
        s=data['sessions'][0]
        self.assertEqual(s['durationMs'],65*MINUTE)
        self.assertEqual(s['visibleMs'],10*MINUTE)
        self.assertEqual(data['days'][0]['demandMs'],10*MINUTE)

    def test_response_requires_endpoints_and_continuous_original_samples(self):
        events=[event(0,10,'NONE'),event(10,60,'HIGH'),event(60,90,'NONE')]
        samples=[[t*MINUTE,v,50] for t,v in [(15,18),(30,19),(45,20),(60,21)]]
        result=self.analyse({1:room(events,samples)})['sessions'][0]
        self.assertEqual(result['response']['delta'],3)
        sparse=self.analyse({1:room(events,[samples[0],samples[-1]])})['sessions'][0]
        self.assertIsNone(sparse['response']['delta'])
        disconnected=self.analyse({1:room(events+[event(30,31,False,'connected')],samples)})
        self.assertEqual(len(disconnected['sessions']),2)

    def test_simulated_energy_and_cost_are_proportional_to_union(self):
        rooms={1:room([event(0,90,'HIGH')]),2:room([event(0,90,'HIGH')])}
        data=self.analyse(rooms,0,60,'demo')
        self.assertEqual(data['costModel'],'illustration')
        self.assertEqual(data['days'][0]['energyKwh'], {'low': 8, 'high': 12})
        self.assertEqual(data['sessions'][0]['energyKwh'], {'low': 12, 'high': 18})
        self.assertAlmostEqual(data['days'][0]['cost']['low'],.64)
        self.assertAlmostEqual(data['days'][0]['cost']['high'],.96)
        real = self.analyse(rooms)
        self.assertIsNone(real['days'][0]['cost'])
        self.assertIsNone(real['days'][0]['energyKwh'])
        self.assertIsNone(real['sessions'][0]['energyKwh'])

    def test_daily_bins_respect_madrid_dst(self):
        a=web.ms('2026-10-24T22:00:00Z');b=web.ms('2026-10-25T23:00:00Z')
        data=analyse({1:room([{'start':a,'end':b,'series':'callForHeat','value':'HIGH'}])},1,a,b,a,b,'real')
        self.assertEqual(len(data['days']),1)
        self.assertEqual(data['days'][0]['demandMs'],25*60*MINUTE)

    def test_invoices_never_expose_private_source_fields(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'invoices.json'
            p.write_text(json.dumps({'invoices':[{'period_start':'2026-01-01','gmail_message_id':'private','file':'/private/file','token':'secret','total_eur':'1.00'}]}))
            with patch.object(web,'INVOICES',p):
                response=web.invoices()
            self.assertFalse(response['completeHistory'])
            text=json.dumps(response)
            for private in ['gmail','/private','secret','token']:
                self.assertNotIn(private,text)

if __name__=='__main__': unittest.main()
