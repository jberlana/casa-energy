"""Fixed provider read jobs for explicit menu actions. No business/device mutations."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import time

from . import environment_history as environment, octopus_api, tado_history as tado
from .octopus_sync import ConfigurationRequired, sync
from .refresh_coordinator import Coordinator


@contextmanager
def lock(root, filename='collector.lock'):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root/filename).open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if shutil.disk_usage(root).free < 1024**3:
            raise OSError('Insufficient free space')
        yield


def refresh_tado(root):
    if not (root/'config.json').exists() or not (root/'history.sqlite3').exists():
        return 3
    with lock(root):
        cfg = tado.DEFAULTS | json.loads((root/'config.json').read_text())
        if not cfg.get('home_id') or not cfg.get('client_id') or not Path(cfg['auth_path']).is_file():
            return 3
        if not Coordinator(history=root).tado_due(time.time(), force=True):
            return 5
        tado.disk_guard(root, cfg)
        db = tado.connect(root, cfg)
        try:
            result = tado.collect(db, root, cfg, force=True)
            tado.atomic_json(root/'status.json', tado.status(db, root, cfg))
            return 0 if result else 4
        finally:
            db.close()


def refresh_hue(root):
    if not environment.AUTH.is_file():
        return 3
    with lock(root):
        db = environment.connect(root)
        try:
            environment.collect_hue(db, int(time.time()*1000))
            if not environment.state(db, 'hue', {}).get('ok'):
                return 1
        except Exception as exc:
            with db:
                old = environment.state(db, 'hue', {})
                environment.set_state(db, 'hue', {**old, 'ok': False, 'failedAt': int(time.time()*1000), 'error': type(exc).__name__})
            raise
        finally:
            db.close()
    return 0


def refresh_octopus(root):
    if not (root/'session.json').is_file():
        return 3
    with lock(root, 'sync.lock'):
        try:
            return sync(root)
        except ConfigurationRequired:
            return 3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provider', choices=['tado', 'hue', 'octopus'])
    args = parser.parse_args()
    os.umask(0o077)
    roots = {'tado': Path(os.environ.get('TADO_DATA_ROOT', tado.ROOT)),
             'hue': Path(os.environ.get('TADO_ENV_ROOT', environment.ROOT)),
             'octopus': Path(os.environ.get('OCTOPUS_ROOT', octopus_api.ROOT))}
    try:
        return {'tado': refresh_tado, 'hue': refresh_hue, 'octopus': refresh_octopus}[args.provider](roots[args.provider])
    except BlockingIOError:
        return 4
    except Exception:
        return 1  # No provider errors, credentials, paths or document URLs enter HTTP responses.


if __name__ == '__main__':
    raise SystemExit(main())
