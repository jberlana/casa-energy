"""Manual refresh actions use synthetic stores and mocked providers only."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from backend import environment_history as hue, provider_refresh as workers
from backend import refresh_coordinator as refresh, tado_history as tado, web_server as web
from backend.octopus_sync import APIError, ConfigurationRequired, SyncClient, sync


class ManualRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1795348800.
        self.calls = []
        self.c = refresh.Coordinator(self.root/'web', self.root/'history', self.root/'environment',
                                     clock=lambda: self.now, runner=self.run_job, octopus=self.root/'octopus')
        self.c.history.mkdir()
        (self.c.history/'config.json').write_text('{}')
        self.db = tado.connect(self.c.history, tado.DEFAULTS)
        self.db.execute("INSERT INTO rooms VALUES (1,1,'Synthetic',1)")
        self.db.commit()

    def tearDown(self):
        if self.c.thread: self.c.thread.join(3)
        self.db.close()
        self.temp.cleanup()

    def run_job(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    def finish(self):
        if self.c.thread: self.c.thread.join(3)

    def test_manual_tado_ignores_staleness_but_preserves_quota(self):
        stamp = refresh.datetime.fromtimestamp(self.now, refresh.timezone.utc).isoformat()
        self.db.execute('INSERT INTO samples VALUES (1,1,?,?,?)', (stamp, 20, 50))
        self.db.execute('INSERT INTO reports VALUES (1,1,?,?,?,?,?,?)', (stamp[:10], stamp, stamp, stamp, 'test', b'{}'))
        self.db.commit()
        self.assertFalse(self.c.tado_due(self.now))
        self.assertTrue(self.c.tado_due(self.now, force=True))
        self.assertTrue(self.c.request_manual('tado')['request']['accepted'])
        self.finish()
        self.assertEqual(self.calls[0][0][-3:], ['-m', 'backend.provider_refresh', 'tado'])
        self.assertEqual(self.calls[0][1]['env']['TADO_DATA_ROOT'], str(self.c.history.resolve()))
        self.assertEqual(self.c.status()['providers']['tado']['state'], 'updated')
        tado.state_set(self.db, 'api_blocked_until', '2099-01-01T00:00:00+00:00')
        self.now += 1801
        self.assertEqual(self.c.request_manual('tado')['request']['reason'], 'server_quota')
        self.assertEqual(len(self.calls), 1)

    def test_manual_limits_persist_and_are_shared_with_automatic_refresh(self):
        with patch.object(self.c, 'tado_due', return_value=True):
            for _ in range(4):
                self.c.request_manual('tado'); self.finish(); self.now += 1801
            self.assertEqual(self.c.request_manual('tado')['request']['reason'], 'web_limit')
        self.assertEqual(len(self.calls), 4)
        state = refresh.read_json(self.c.root/'refresh.json')
        self.assertEqual(state['tadoCount'], 4)
        self.c.request_manual('hue'); self.finish()
        restarted = refresh.Coordinator(self.c.root, self.c.history, self.c.environment, clock=lambda: self.now, runner=self.run_job, octopus=self.root/'octopus')
        self.assertEqual(restarted.request_manual('hue')['request']['reason'], 'cooldown')

    def test_cross_tab_and_automatic_concurrency_never_start_duplicate_work(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*a, **kw): entered.set(); release.wait(2); return SimpleNamespace(returncode=0)
        self.c.runner = slow
        self.c.request_manual('hue'); entered.wait(1)
        try:
            other = refresh.Coordinator(self.c.root, self.c.history, self.c.environment, clock=lambda: self.now, runner=self.run_job, octopus=self.root/'octopus')
            self.assertEqual(other.request_manual('octopus')['request']['reason'], 'busy')
            self.assertTrue(other.request()['running'])
            self.assertEqual(self.calls, [])
        finally:
            release.set(); self.finish()

    def test_failed_partial_and_unconfigured_results_are_truthful(self):
        for code, expected in [(1, 'failed'), (3, 'unavailable'), (4, 'busy'), (6, 'pending_review'), (7, 'partial')]:
            self.c.runner = lambda *a, **k: SimpleNamespace(returncode=code)
            self.c.request_manual('octopus'); self.finish()
            self.assertEqual(self.c.status()['providers']['octopus']['state'], expected)
            self.now += 901
        state = self.c.status()
        self.assertNotIn('command', json.dumps(state))
        self.assertNotIn(str(self.root), json.dumps(state))

    def test_general_refresh_reserves_each_provider_and_retains_independent_results(self):
        self.c.runner = lambda command, **kw: (self.calls.append(command[-1]) or
            SimpleNamespace(returncode={'tado': 1, 'hue': 0, 'octopus': 6}[command[-1]]))
        result = self.c.request_manual('all')
        self.finish()
        self.assertTrue(result['request']['accepted'])
        self.assertCountEqual(self.calls, ['tado', 'hue', 'octopus'])
        state = self.c.status()['providers']
        self.assertEqual(state['tado']['state'], 'failed')
        self.assertIsNone(state['tado']['lastSuccess'])
        self.assertEqual(state['hue']['lastSuccess'], int(self.now*1000))
        self.assertEqual(state['octopus']['state'], 'pending_review')
        again = self.c.request_manual('all')
        self.assertFalse(again['request']['accepted'])
        self.assertTrue(all(d['reason'] == 'cooldown' for d in again['request']['decisions'].values()))
        self.assertEqual(len(self.calls), 3)

    def test_general_refresh_keeps_one_lock_and_reports_all_running_providers(self):
        release = threading.Event()
        def run(*a, **kw):
            release.wait(3)
            return SimpleNamespace(returncode=0)
        self.c.runner = run
        try:
            self.c.request_manual('all')
            state = self.c.status()
            self.assertTrue(state['running'])
            self.assertTrue(all(p['state'] == 'running' for p in state['providers'].values()))
            self.assertEqual(self.c.request_manual('hue')['request']['reason'], 'busy')
            self.assertTrue(self.c.request()['running'])
        finally:
            release.set(); self.finish()

    def test_general_refresh_skips_tado_quota_without_blocking_other_providers(self):
        with patch.object(self.c, 'tado_budget', return_value={'allowed': False, 'reason': 'local_budget', 'retryAt': None}):
            result = self.c.request_manual('all')
            self.finish()
        self.assertEqual(result['request']['decisions']['tado']['reason'], 'local_budget')
        self.assertCountEqual([c[0][-1] for c in self.calls], ['hue', 'octopus'])
        self.assertEqual(refresh.read_json(self.c.root/'refresh.json')['tadoCount'], 0)

    def test_failed_retry_preserves_success_and_scheduled_collection_is_visible(self):
        self.c.request_manual('hue'); self.finish()
        successful = self.c.status()['providers']['hue']['lastSuccess']
        self.now += 61
        self.c.runner = lambda *a, **kw: SimpleNamespace(returncode=1)
        self.c.request_manual('hue'); self.finish()
        self.assertEqual(self.c.status()['providers']['hue']['lastSuccess'], successful)
        self.now += 1
        stamp = refresh.datetime.fromtimestamp(self.now, refresh.timezone.utc).isoformat()
        tado.state_set(self.db, 'last_success', stamp)
        self.db.commit()
        with closing(hue.connect(self.c.environment)) as db:
            db.execute("INSERT INTO outdoor VALUES (?, 10, ?, 'Synthetic', 1)", (int(self.now*1000), int(self.now*1000)))
            db.execute("INSERT INTO outdoor VALUES (?, NULL, NULL, 'Synthetic', 0)", (int((self.now+5)*1000),))
            db.commit()
        self.c.octopus = self.root/'octopus'
        self.c.octopus.mkdir()
        (self.c.octopus/'catalog.json').write_text(json.dumps({'summary': {'updated': int(self.now*1000)}}))
        state = self.c.status()['providers']
        for provider in ('tado', 'hue', 'octopus'):
            self.assertEqual(state[provider]['lastSuccess'], int(self.now*1000))
        self.assertEqual(state['hue']['state'], 'updated')

    def test_unknown_provider_cannot_become_a_command(self):
        with self.assertRaises(ValueError): self.c.request_manual('../../anything')
        self.assertEqual(self.calls, [])

    def test_abandoned_job_is_interrupted_after_deadline(self):
        self.c.root.mkdir()
        refresh.write_json(self.c.root/'refresh.json', {'running': True, 'started': self.now-241, 'manual': 'hue',
                                                     'providers': {'hue': {'state': 'running'}}})
        self.assertFalse(self.c.status()['running'])
        self.assertEqual(self.c.status()['providers']['hue']['state'], 'interrupted')

    def test_manual_endpoint_rejects_demo_cross_origin_and_missing_header(self):
        def request(origin='http://localhost', header='1'):
            return Request({'type': 'http', 'headers': [(b'host', b'localhost'), (b'origin', origin.encode()), (b'x-casa-refresh', header.encode())]})
        with patch.object(web.refresh_coordinator, 'request_manual', return_value={'running': True}) as action:
            for req, source in [(request(), 'demo'), (request('https://outside.invalid'), 'real'), (request(header=''), 'real')]:
                with self.assertRaises(HTTPException): web.refresh_provider(req, 'hue', source)
            action.assert_not_called()
            self.assertTrue(web.refresh_provider(request(), 'hue', 'real')['running'])
            action.assert_called_once_with('hue')


class FakeOctopus:
    def __init__(self, count=5):
        self.downloads = []
        self.operations = []
        self.count = count
    def read(self, operation, **kwargs):
        self.operations.append(operation)
        if operation == 'accounts': return {'viewer': {'accounts': [{'number': 'synthetic'}]}}
        if operation == 'overview': return {'account': {'ledgers': [{'number': 'gas-test', 'ledgerType': 'GAS'}]}}
        if operation == 'bills':
            return {'account': {'bills': {'pageInfo': {'hasNextPage': True}, 'edges': [{'node': {
                'id': str(i), 'billType': 'INVOICE', 'isAnnulled': False, 'issuedDate': f'2026-01-{i+1:02d}', 'grossAmount': '20'}} for i in range(self.count)]}}}
        raise AssertionError(operation)
    def download_bill(self, account, bill, destination):
        self.downloads.append(bill)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination/('octopus-' + hashlib.sha256(bill.encode()).hexdigest()[:16] + '.pdf')
        path.write_bytes(b'%PDF-synthetic')
        return {'path': str(path)}


class ProviderWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def test_hue_only_updates_hue_and_offline_is_not_success(self):
        auth = self.root/'auth.json'; auth.write_text('{}')
        def collect(db, now):
            with db: hue.set_state(db, 'hue', {'ok': True, 'updated': now})
        with patch.object(hue, 'AUTH', auth), patch.object(hue, 'collect_hue', side_effect=collect), patch.object(hue, 'collect_weather') as weather:
            self.assertEqual(workers.refresh_hue(self.root), 0)
            weather.assert_not_called()
        with patch.object(hue, 'AUTH', auth), patch.object(hue, 'collect_hue', side_effect=lambda db, now: hue.set_state(db, 'hue', {'ok': False})):
            self.assertEqual(workers.refresh_hue(self.root), 1)

    def test_missing_provider_credentials_never_trigger_network(self):
        with patch.object(workers, 'sync') as octopus, patch.object(tado, 'collect') as heating, patch.object(hue, 'AUTH', self.root/'missing'), patch.object(hue, 'collect_hue') as bridge:
            self.assertEqual(workers.refresh_tado(self.root), 3)
            self.assertEqual(workers.refresh_hue(self.root), 3)
            self.assertEqual(workers.refresh_octopus(self.root), 3)
            octopus.assert_not_called(); heating.assert_not_called(); bridge.assert_not_called()

    def test_octopus_downloads_three_then_continues_without_duplicates(self):
        client = FakeOctopus()
        self.assertEqual(sync(self.root, client), 7)
        self.assertEqual(len(client.downloads), 3)
        self.assertEqual(sync(self.root, client), 6)
        self.assertEqual(len(client.downloads), 5)
        self.assertEqual(sync(self.root, client), 6)
        self.assertEqual(len(client.downloads), 5)
        catalog = json.loads((self.root/'catalog.json').read_text())
        self.assertTrue(catalog['summary']['requiresVerification'])
        self.assertTrue(catalog['summary']['moreHistory'])
        self.assertEqual(catalog['summary']['remaining'], 0)
        self.assertEqual((self.root/'catalog.json').stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root/'verified-invoices.json').exists())

    def test_octopus_failure_preserves_catalog_and_verified_invoices(self):
        sync(self.root, FakeOctopus(1))
        before = (self.root/'catalog.json').read_bytes()
        verified = self.root/'verified-invoices.json'; verified.write_text('{"synthetic":true}')
        client = FakeOctopus(2)
        with patch.object(client, 'download_bill', side_effect=APIError('synthetic failure')):
            with self.assertRaises(APIError): sync(self.root, client)
        self.assertEqual((self.root/'catalog.json').read_bytes(), before)
        self.assertEqual(verified.read_text(), '{"synthetic":true}')

    def test_octopus_ambiguous_account_or_gas_ledger_requires_configuration(self):
        client = FakeOctopus()
        with patch.object(client, 'read', return_value={'viewer': {'accounts': [{'number': 'a'}, {'number': 'b'}]}}):
            with self.assertRaises(ConfigurationRequired): sync(self.root, client)
        original = client.read
        with patch.object(client, 'read', side_effect=lambda op, **kw: {'account': {'ledgers': []}} if op == 'overview' else original(op, **kw)):
            with self.assertRaises(ConfigurationRequired): sync(self.root, client)

    def test_octopus_storage_and_query_budgets_stop_before_more_downloads(self):
        client = FakeOctopus()
        with patch('backend.octopus_sync.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaises(APIError): sync(self.root, client)
        self.assertEqual(client.downloads, [])
        bounded = SyncClient(self.root)
        bounded.requests = 12
        with patch.object(bounded.opener, 'open') as network:
            with self.assertRaises(APIError): bounded._request('accounts')
            network.assert_not_called()


if __name__ == '__main__': unittest.main()
