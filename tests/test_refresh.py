from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException
import tado_history as tado
import refresh_coordinator as refresh
import web_server as web


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.now=1795348800.
        self.calls=[]
        self.c=refresh.Coordinator(self.base/'web',self.base/'tado',self.base/'env',clock=lambda:self.now,runner=self.run_job)
        self.c.root.mkdir();self.c.history.mkdir();self.c.environment.mkdir()
        (self.c.history/'bin').mkdir();(self.c.history/'bin/tado_history.py').touch()
        (self.c.environment/'environment_history.py').touch()
        self.db=tado.connect(self.c.history,tado.DEFAULTS)
        self.db.execute("INSERT INTO rooms VALUES (1,1,'Room',1)");self.db.commit()
    def tearDown(self):
        if self.c.thread:self.c.thread.join(3)
        self.db.close();self.temp.cleanup()
    def run_job(self,command,**kwargs):
        self.calls.append(command);return SimpleNamespace(returncode=0)
    def finish(self):
        if self.c.thread:self.c.thread.join(3)
    def test_fresh_sources_do_not_launch_any_collectors(self):
        with patch.object(self.c,'tado_due',return_value=False),patch.object(self.c,'environment_due',return_value=False):
            self.assertFalse(self.c.request()['running'])
        self.assertEqual(self.calls,[])
    def test_single_flight_across_instances_and_tabs(self):
        entered=threading.Event();release=threading.Event()
        def slow(*args,**kwargs): entered.set();release.wait(2);return SimpleNamespace(returncode=0)
        self.c.runner=slow
        with patch.object(self.c,'tado_due',return_value=True),patch.object(self.c,'environment_due',return_value=False):
            self.assertTrue(self.c.request()['running']);entered.wait(1)
            other=refresh.Coordinator(self.c.root,self.c.history,self.c.environment,clock=lambda:self.now,runner=self.run_job)
            self.assertTrue(other.request()['running']);self.assertEqual(self.calls,[])
            release.set();self.finish()
        self.assertFalse(self.c.status()['running'])
    def test_cooldown_persists_after_restart_even_on_failure(self):
        self.c.runner=lambda *a,**k:SimpleNamespace(returncode=1)
        with patch.object(self.c,'tado_due',return_value=True),patch.object(self.c,'environment_due',return_value=False):
            self.c.request();self.finish()
        other=refresh.Coordinator(self.c.root,self.c.history,self.c.environment,clock=lambda:self.now+60,runner=self.run_job)
        with patch.object(other,'tado_due',return_value=True),patch.object(other,'environment_due',return_value=False):other.request()
        self.assertEqual(self.calls,[])
        self.assertEqual(refresh.read_json(self.c.root/'refresh.json')['results']['tado'],'failed')
    def test_daily_limit_and_new_day(self):
        with patch.object(self.c,'tado_due',return_value=True),patch.object(self.c,'environment_due',return_value=False):
            for _ in range(5):self.c.request();self.finish();self.now+=1801
            self.assertEqual(len(self.calls),4)
            self.now+=86400;self.c.request();self.finish();self.assertEqual(len(self.calls),5)
    def test_collector_is_fixed_and_bounded(self):
        with patch.object(self.c,'tado_due',return_value=True),patch.object(self.c,'environment_due',return_value=True):
            self.c.request();self.finish()
        self.assertEqual(len(self.calls),2)
        self.assertTrue(any(c[-2:]==['collect','--force'] for c in self.calls))
        self.assertEqual((self.c.root/'refresh.json').stat().st_mode & 0o777,0o600)
    def test_tado_quota_pause_and_schedule_reserve(self):
        self.assertTrue(self.c.tado_due(self.now))
        tado.state_set(self.db,'api_blocked_until','2099-01-01T00:00:00+00:00')
        self.assertFalse(self.c.tado_due(self.now))
        tado.state_set(self.db,'api_blocked_until',None)
        tado.state_set(self.db,'server_quota',{'remaining':22})
        self.assertFalse(self.c.tado_due(self.now))
        tado.state_set(self.db,'server_quota',{'remaining':100})
        day=refresh.datetime.fromtimestamp(self.now,refresh.timezone.utc).date().isoformat()
        self.db.execute('INSERT INTO request_counts VALUES (?,?)',(day,65));self.db.commit()
        self.assertFalse(self.c.tado_due(self.now))
    def test_fresh_reports_and_samples_do_not_trigger_tado(self):
        stamp=refresh.datetime.fromtimestamp(self.now-60,refresh.timezone.utc).isoformat()
        self.db.execute('INSERT INTO samples VALUES (1,1,?,?,?)',(stamp,20,50))
        self.db.execute('INSERT INTO reports VALUES (1,1,?,?,?,?,?,?)',(stamp[:10],stamp,stamp,stamp,'test',b'{}'));self.db.commit()
        self.assertFalse(self.c.tado_due(self.now))
        self.now+=3600;self.assertTrue(self.c.tado_due(self.now))
    def test_refresh_post_rejects_cross_origin_and_missing_header(self):
        for origin,header in [('https://evil.invalid','1'),('http://localhost','')]:
            req=Request({'type':'http','headers':[(b'host',b'localhost'),(b'origin',origin.encode()),(b'x-casa-refresh',header.encode())]})
            with self.assertRaises(HTTPException):web.refresh_if_stale(req)
        req=Request({'type':'http','headers':[(b'host',b'localhost'),(b'origin',b'http://localhost'),(b'x-casa-refresh',b'1')]})
        with patch.object(web.refresh_coordinator,'request',return_value={'running':False,'revision':1}) as call:
            self.assertEqual(web.refresh_if_stale(req)['revision'],1);call.assert_called_once()

if __name__=='__main__':unittest.main()
