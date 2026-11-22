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
    def __init__(self, root=None, history=None, environment=None, clock=time.time, runner=subprocess.run):
        self.root = Path(root or os.environ.get('TADO_WEB_ROOT',HOME/'TadoWeb'))
        self.history = Path(history or os.environ.get('TADO_DATA_ROOT',HOME/'TadoHistory'))
        self.environment = Path(environment or os.environ.get('TADO_ENV_ROOT',HOME/'TadoEnvironment'))
        self.clock, self.runner = clock, runner
        self.thread = None

    def status(self):
        s=read_json(self.root/'refresh.json')
        running = bool(s.get('running') and self.clock()-s.get('started',0)<240)
        providers = {name: dict(s.get('providers', {}).get(name, {'state': 'idle'})) for name in ('tado', 'hue', 'octopus')}
        for name, value in providers.items():
            if value.get('state') == 'running' and (not running or s.get('manual') != name):
                value['state'] = 'interrupted'
        return {'running':running, 'revision':s.get('revision',0), 'providers':providers}

    def tado_due(self, now, force=False):
        path=self.history/'history.sqlite3'
        cfg=read_json(self.history/'config.json')
        if not path.exists(): return False
        try:
            with closing(sqlite3.connect(f'file:{path}?mode=ro',uri=True,timeout=2)) as db:
                db.execute('BEGIN')
                count=db.execute('SELECT count(*) FROM rooms WHERE active=1').fetchone()[0]
                rows=db.execute('SELECT (SELECT max(fetched_at) FROM reports p WHERE p.home_id=r.home_id AND p.room_id=r.room_id), '
                                '(SELECT max(timestamp) FROM samples s WHERE s.home_id=r.home_id AND s.room_id=r.room_id) '
                                'FROM rooms r WHERE active=1').fetchall()
                stale=not rows or any(now-epoch(fetched)>30*MINUTE or now-epoch(sample)>45*MINUTE for fetched,sample in rows)
                states={k:json.loads(v) for k,v in db.execute("SELECT key,value FROM state WHERE key IN ('api_blocked_until','server_quota')")}
                if epoch(states.get('api_blocked_until'))>now: return False
                day=datetime.fromtimestamp(now,timezone.utc).date().isoformat()
                used=db.execute('SELECT COALESCE(SUM(count),0) FROM request_counts WHERE day=?',(day,)).fetchone()[0]
                # Preserve a conservative full day's scheduled allowance, including auth/metadata.
                cycle=max(1,count)+3
                scheduled=len(cfg.get('slots',[1,2,3,4]))*cycle
                if used+cycle+scheduled>cfg.get('daily_request_limit',80): return False
                remaining=states.get('server_quota',{}).get('remaining')
                if remaining is not None and remaining < cfg.get('server_quota_reserve',20)+cycle+scheduled: return False
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
        if provider not in ('tado', 'hue', 'octopus'):
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
            cooldown = {'tado': 30*MINUTE, 'hue': MINUTE, 'octopus': 15*MINUTE}[provider]
            attempt_key = {'tado': 'tadoAttempt', 'hue': 'hueAttempt', 'octopus': 'octopusAttempt'}[provider]
            last = state.get(attempt_key, 0)
            if provider == 'hue':
                last = max(last, state.get('environmentAttempt', 0))
            reason = None
            if now - last < cooldown:
                reason = 'cooldown'
            elif provider == 'tado' and (not (self.history/'config.json').exists() or not (self.history/'history.sqlite3').exists()):
                reason = 'unavailable'
            elif provider == 'tado' and (state.get('tadoCount', 0) >= 4 or not self.tado_due(now, force=True)):
                reason = 'quota'
            if reason:
                return {**self.status(), 'request': {'provider': provider, 'accepted': False, 'reason': reason,
                        'retryAt': int((last+cooldown)*1000) if reason == 'cooldown' else None}}
            state[attempt_key] = now
            if provider == 'tado':
                state['tadoCount'] = state.get('tadoCount', 0)+1
            state['providers'][provider] = {'state': 'running', 'at': int(now*1000)}
            state.update(running=True, started=now, manual=provider)
            write_json(self.root/'refresh.json', state)
            command = [sys.executable, '-m', 'backend.provider_refresh', provider]
            self.thread = threading.Thread(target=self._work, args=([(provider, command, 190)], state, lock), daemon=True)
            self.thread.start()
            handed_off = True
            return {**self.status(), 'request': {'provider': provider, 'accepted': True}}
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
                                       'TADO_DATA_ROOT': str(self.history.resolve()), 'TADO_ENV_ROOT': str(self.environment.resolve())}}
                result=self.runner(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,timeout=timeout,check=False,**options)
                if state.get('manual'):
                    return name, {0: 'updated', 3: 'unavailable', 4: 'busy', 5: 'quota',
                                  6: 'pending_review', 7: 'partial'}.get(result.returncode, 'failed')
                return name, 'finished' if result.returncode==0 else 'failed'
            except (OSError,subprocess.TimeoutExpired):
                return name,'failed'
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                state['results']=dict(pool.map(run,jobs))
                if state.get('manual'):
                    provider = state['manual']
                    state['providers'][provider] = {'state': state['results'].get(provider, 'failed'), 'at': int(self.clock()*1000)}
        finally:
            state.update(running=False,revision=max(state.get('revision', 0)+1, int(self.clock()*1000)))
            try: write_json(self.root/'refresh.json',state)
            finally: lock.close()
