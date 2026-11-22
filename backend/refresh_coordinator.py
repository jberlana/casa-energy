"""Bounded, single-flight collection requested by a visible local web client."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from . import tado_quota

HOME = Path.home() / 'Library/Application Support'
MINUTE = 60


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.refresh-')
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(value,out)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def epoch(value):
    if not value: return 0
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


class Coordinator:
    def __init__(self, root=None, history=None, environment=None, clock=time.time, runner=subprocess.run, octopus=None):
        self.root = Path(root or os.environ.get('TADO_WEB_ROOT',HOME/'TadoWeb'))
        self.history = Path(history or os.environ.get('TADO_DATA_ROOT',HOME/'TadoHistory'))
        self.environment = Path(environment or os.environ.get('TADO_ENV_ROOT',HOME/'TadoEnvironment'))
        self.octopus = Path(octopus or os.environ.get('OCTOPUS_ROOT', Path.home()/'.config/octopus-experiment'))
        self.clock, self.runner = clock, runner
        self.thread = None

    def status(self):
        s=read_json(self.root/'refresh.json')
        running = bool(s.get('running') and self.clock()-s.get('started',0)<240)
        providers = {name: dict(s.get('providers', {}).get(name, {'state': 'idle'})) for name in ('tado', 'hue', 'octopus')}
        for name, value in providers.items():
            if value.get('state') == 'running' and (not running or s.get('manual') not in (name, 'all')):
                value['state'] = 'interrupted'
        for name, value in providers.items():
            value['lastSuccess'] = max(value.get('lastSuccess') or 0, self.last_success(name)) or None
            if (name in ('tado', 'hue') and value.get('state') != 'running'
                    and (value['lastSuccess'] or 0) > value.get('at', 0)):
                value['state'] = 'updated'
        providers['tado']['availability'] = self.tado_admission(self.clock(), s)
        return {'running':running, 'revision':s.get('revision',0), 'providers':providers}

    def last_success(self, provider):
        # Read collector-owned evidence, including scheduled refreshes. Attempts are not successes.
        try:
            if provider == 'octopus':
                return int(read_json(self.octopus/'catalog.json').get('summary', {}).get('updated') or 0)
            path = self.history/'history.sqlite3' if provider == 'tado' else self.environment/'environment.sqlite3'
            if not path.is_file(): return 0
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=.2)) as db:
                if provider == 'hue':
                    return db.execute('SELECT max(timestamp) FROM outdoor WHERE valid=1').fetchone()[0] or 0
                row = db.execute("SELECT value FROM state WHERE key='last_success'").fetchone()
                return int(epoch(json.loads(row[0]))*1000) if row else 0
        except (OSError, sqlite3.Error, ValueError, TypeError):
            return 0

    def tado_budget(self, now):
        path = self.history/'history.sqlite3'
        if not path.is_file() or not (self.history/'config.json').is_file():
            return {'allowed': False, 'reason': 'unavailable', 'retryAt': None}
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=2)) as db:
                db.execute('BEGIN')
                rooms = db.execute('SELECT count(*) FROM rooms WHERE active=1').fetchone()[0]
                return tado_quota.budget(db, read_json(self.history/'config.json'), now,
                                         needed=max(1, rooms)+3, scheduled=True)
        except (sqlite3.Error, ValueError, TypeError, KeyError):
            return {'allowed': False, 'reason': 'unavailable', 'retryAt': None}

    def tado_admission(self, now, state):
        decision = self.tado_budget(now)
        if not decision['allowed']: return decision
        if now-state.get('tadoAttempt', 0) < 30*MINUTE:
            return {**decision, 'allowed': False, 'reason': 'cooldown',
                    'retryAt': int((state['tadoAttempt']+30*MINUTE)*1000)}
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        if state.get('day') == day and state.get('tadoCount', 0) >= 4:
            midnight = datetime.fromtimestamp(now, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()+86400
            return {**decision, 'allowed': False, 'reason': 'web_limit', 'retryAt': int(midnight*1000)}
        return decision

    def tado_due(self, now, force=False):
        path=self.history/'history.sqlite3'
        if not path.exists(): return False
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=2)) as db:
                db.execute('BEGIN')
                rows=db.execute('SELECT (SELECT max(fetched_at) FROM reports p WHERE p.home_id=r.home_id AND p.room_id=r.room_id), '
                                '(SELECT max(timestamp) FROM samples s WHERE s.home_id=r.home_id AND s.room_id=r.room_id) '
                                'FROM rooms r WHERE active=1').fetchall()
                stale=not rows or any(now-epoch(fetched)>30*MINUTE or now-epoch(sample)>45*MINUTE for fetched,sample in rows)
                if not self.tado_budget(now)['allowed']: return False
                return force or stale
        except (sqlite3.Error,ValueError,TypeError):
            return False

    def environment_due(self, now):
        path=self.environment/'environment.sqlite3'
        if not path.exists(): return True
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=2)) as db:
                values={k:json.loads(v) for k,v in db.execute("SELECT key,value FROM state WHERE key IN ('hue','weather_attempt')")}
            return now-values.get('hue',{}).get('updated',0)/1000>6*MINUTE or now-values.get('weather_attempt',0)/1000>3*3600
        except (sqlite3.Error,ValueError,TypeError):
            return False

    def request(self):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        lock=(self.root/'refresh.lock').open('a')
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return self.status()
        try:
            now=self.clock()
            state=read_json(self.root/'refresh.json')
            day=datetime.fromtimestamp(now,timezone.utc).date().isoformat()
            if state.get('day')!=day:
                state['day']=day;state['tadoCount']=0
            jobs=[]
            tado=self.history/'bin/tado_history.py'
            env=self.environment/'environment_history.py'
            if (tado.is_file() and now-state.get('tadoAttempt',0)>=30*MINUTE
                    and state.get('tadoCount',0)<4 and self.tado_due(now)):
                jobs.append(('tado',[sys.executable,str(tado),'--root',str(self.history),'collect','--force'],190))
                state['tadoAttempt']=now;state['tadoCount']=state.get('tadoCount',0)+1
            if env.is_file() and now-state.get('environmentAttempt',0)>=15*MINUTE and self.environment_due(now):
                jobs.append(('environment',[sys.executable,str(env)],100))
                state['environmentAttempt']=now
            if not jobs:
                # Recover an interrupted status without starting any unbounded retry.
                if state.get('running'):
                    state['running']=False;state['revision']=int(now*1000)
                    write_json(self.root/'refresh.json',state)
                lock.close()
                return self.status()
            state.update(running=True,started=now,manual=None)
            write_json(self.root/'refresh.json',state)  # Reserve before starting; persists across restarts/tabs.
            self.thread=threading.Thread(target=self._work,args=(jobs,state,lock),daemon=True)
            self.thread.start()
            return self.status()
        except Exception:
            lock.close()
            raise

    def request_manual(self, provider):
        if provider not in ('tado', 'hue', 'octopus', 'all'):
            raise ValueError('Unknown provider')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (self.root/'refresh.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            return {**self.status(), 'request': {'provider': provider, 'accepted': False, 'reason': 'busy'}}
        handed_off = False
        try:
            now = self.clock()
            state = read_json(self.root/'refresh.json')
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            if state.get('day') != day:
                state.update(day=day, tadoCount=0)
            state.setdefault('providers', {})
            jobs, decisions = [], {}
            selected = ('tado', 'hue', 'octopus') if provider == 'all' else (provider,)
            for name in selected:
                cooldown = {'tado': 30*MINUTE, 'hue': MINUTE, 'octopus': 15*MINUTE}[name]
                attempt_key = {'tado': 'tadoAttempt', 'hue': 'hueAttempt', 'octopus': 'octopusAttempt'}[name]
                last = state.get(attempt_key, 0)
                if name == 'hue':
                    last = max(last, state.get('environmentAttempt', 0))
                reason, retry_at = None, None
                if name == 'tado':
                    decision = self.tado_admission(now, state)
                    reason, retry_at = decision['reason'], decision['retryAt']
                elif now - last < cooldown:
                    reason, retry_at = 'cooldown', int((last+cooldown)*1000)
                decisions[name] = {'accepted': reason is None, 'reason': reason, 'retryAt': retry_at}
                if reason:
                    continue
                state[attempt_key] = now
                if name == 'tado':
                    state['tadoCount'] = state.get('tadoCount', 0)+1
                previous = state['providers'].get(name, {})
                state['providers'][name] = {**previous, 'state': 'running', 'at': int(now*1000)}
                jobs.append((name, [sys.executable, '-m', 'backend.provider_refresh', name], 190))
            response = {'provider': provider, 'accepted': bool(jobs), 'decisions': decisions}
            if provider != 'all': response.update(decisions[provider])
            if not jobs:
                return {**self.status(), 'request': response}
            state.update(running=True, started=now, manual=provider)
            write_json(self.root/'refresh.json', state)
            self.thread = threading.Thread(target=self._work, args=(jobs, state, lock), daemon=True)
            self.thread.start()
            handed_off = True
            return {**self.status(), 'request': response}
        finally:
            if not handed_off:
                lock.close()

    def _work(self,jobs,state,lock):
        def run(job):
            name,command,timeout=job
            try:
                options = {}
                if state.get('manual'):
                    options = {'cwd': str(Path(__file__).resolve().parents[1]),
                               'env': {**os.environ, 'TADO_WEB_ROOT': str(self.root.resolve()),
                                       'TADO_DATA_ROOT': str(self.history.resolve()), 'TADO_ENV_ROOT': str(self.environment.resolve()),
                                       'OCTOPUS_ROOT': str(self.octopus.resolve())}}
                result=self.runner(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,timeout=timeout,check=False,**options)
                if state.get('manual'):
                    return name, {0: 'updated', 3: 'unavailable', 4: 'busy', 5: 'quota',
                                  6: 'pending_review', 7: 'partial'}.get(result.returncode, 'failed'), int(self.clock()*1000)
                return name, 'finished' if result.returncode==0 else 'failed', int(self.clock()*1000)
            except (OSError,subprocess.TimeoutExpired):
                return name, 'failed', int(self.clock()*1000)
        try:
            with ThreadPoolExecutor(max_workers=3) as pool:
                outcomes = list(pool.map(run, jobs))
                state['results'] = {name: result for name, result, _ in outcomes}
                if state.get('manual'):
                    for provider, result, at in outcomes:
                        previous = state['providers'].get(provider, {})
                        state['providers'][provider] = {**previous, 'state': result, 'at': at}
                        if result in ('updated', 'pending_review', 'partial'):
                            state['providers'][provider]['lastSuccess'] = at
        finally:
            state.update(running=False,revision=max(state.get('revision', 0)+1, int(self.clock()*1000)))
            try: write_json(self.root/'refresh.json',state)
            finally: lock.close()
