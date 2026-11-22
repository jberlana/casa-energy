"""Installation path contracts tested without touching user services or collecting data."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
from struct import pack
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.macos import install_web_service as installer

REPO = Path(__file__).resolve().parents[1]


class LayoutTests(unittest.TestCase):
    def test_module_entrypoints_show_help_without_config_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {**os.environ, 'HOME': directory}
            for module in ['backend.tado_history', 'backend.octopus_api', 'backend.window_calibration', 'backend.provider_refresh']:
                result = subprocess.run([sys.executable, '-m', module, '--help'], cwd=REPO,
                                        env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_web_install_copies_importable_package(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home/'Library').mkdir()
            project, installed = home/'project', home/'installed'
            (project/'web/dist').mkdir(parents=True)
            template = (REPO/'web/index.html').read_text(encoding='utf-8')
            (project/'web/dist/index.html').write_text(template, encoding='utf-8')
            (project/'requirements-web.lock').write_text('')
            shutil.copytree(REPO/'backend', project/'backend', ignore=shutil.ignore_patterns('__pycache__'))
            (installed/'venv/bin').mkdir(parents=True)
            (installed/'venv/bin/python').touch()
            (installed/'branding.json').write_text(json.dumps({'name': 'Fixture & "Room"'}))
            png_header = b"\x89PNG\r\n\x1a\n" + pack(">I4sII", 13, b"IHDR", 180, 180)
            (installed/'apple-touch-icon.png').write_bytes(png_header)
            def command(args, **kwargs):
                return SimpleNamespace(returncode=int(args[:2] == ['/bin/launchctl','print']), stdout='')
            with patch.object(installer,'ROOT',installed), patch.object(installer,'PROJECT',project), \
                    patch.object(installer,'TAILSCALE',str(home/'missing')), patch.object(Path,'home',return_value=home), \
                    patch.object(installer.subprocess,'run',side_effect=command):
                old_umask=os.umask(0o077)
                try: installer.main()
                finally: os.umask(old_umask)
            plist=plistlib.loads((home/'Library/LaunchAgents/local.tado-history.web.plist').read_bytes())
            self.assertEqual(plist['ProgramArguments'][1:],['-m','backend.web_server'])
            self.assertEqual(plist['WorkingDirectory'],str(installed))
            html = (installed/'dist/index.html').read_text(encoding='utf-8')
            self.assertIn('<title>Fixture &amp; &quot;Room&quot;</title>', html)
            self.assertIn('rel="apple-touch-icon" sizes="180x180"', html)
            self.assertEqual((installed/'dist/apple-touch-icon.png').read_bytes(), png_header)
            self.assertEqual((project/'web/dist/index.html').read_text(encoding='utf-8'), template)
            result=subprocess.run([sys.executable,'-c','from backend.web_server import app; print(app.title)'],
                                  cwd=installed, env={**os.environ,'TADO_WEB_ROOT':str(installed),
                                  'TADO_WEB_DIST':str(installed/'dist')},capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertTrue((installed/'backend/__init__.py').is_file())
            result = subprocess.run([sys.executable, '-m', 'backend.provider_refresh', '--help'], cwd=installed,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((installed/'history.sqlite3').exists())

    def test_wake_preparation_uses_project_stage_and_packaged_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            project=Path(directory)
            target=project/'scripts/macos'
            target.mkdir(parents=True)
            for name in ['prepare_wake_install.py','wake_helper.sh']:
                shutil.copy2(REPO/'scripts/macos'/name,target/name)
            result=subprocess.run([sys.executable,str(target/'prepare_wake_install.py')],
                                  capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            text=(project/'.install/install_wake.sh').read_text()
            self.assertIn(str(target/'wake_helper.sh'),text)
            self.assertFalse((target/'.install').exists())


if __name__ == '__main__': unittest.main()
