#!/usr/bin/env python3
"""Install the built prototype as a user service, separately from the collector."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

LABEL = "local.tado-history.web"
ROOT = Path.home() / "Library/Application Support/TadoWeb"
PROJECT = Path(__file__).resolve().parent
TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def main():
    os.umask(0o077)
    if not (PROJECT / "web/dist/index.html").is_file():
        raise SystemExit("Build first: npm ci --prefix web && npm run build --prefix web")
    root = ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = root / "venv"
    if not (env / "bin/python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
    subprocess.run([str(env / "bin/pip"), "install", "--quiet", "--disable-pip-version-check", "-r",
                    str(PROJECT / "requirements-web.lock")], check=True)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", domain + "/" + LABEL], capture_output=True)
    # bootout returns before an exiting process has completely left the domain.
    for _ in range(100):
        if subprocess.run(["/bin/launchctl", "print", domain + "/" + LABEL], capture_output=True).returncode:
            break
        time.sleep(.2)
    else:
        raise SystemExit("Previous web service is still stopping; retry installation shortly")
    shutil.copy2(PROJECT / "web_server.py", root / "web_server.py")
    shutil.copy2(PROJECT / "refresh_coordinator.py", root / "refresh_coordinator.py")
    shutil.copy2(PROJECT / "environment_history.py", root / "environment_history.py")
    shutil.copy2(PROJECT / "heating_sessions.py", root / "heating_sessions.py")
    verified = PROJECT / "output/octopus/verified-invoices.json"
    if verified.exists():
        shutil.copy2(verified, root / "verified-invoices.json")
        (root / "verified-invoices.json").chmod(0o600)
    # Replace only this application's static build; history and collector are separate.
    shutil.rmtree(root / "dist", ignore_errors=True)
    shutil.copytree(PROJECT / "web/dist", root / "dist")
    hostname = ""
    if Path(TAILSCALE).exists():
        result = subprocess.run([TAILSCALE, "status", "--json"], capture_output=True, text=True)
        if result.returncode == 0:
            hostname = json.loads(result.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
    plist = {
        "Label": LABEL,
        "ProgramArguments": [str(env / "bin/python"), str(root / "web_server.py")],
        "WorkingDirectory": str(root), "RunAtLoad": True, "KeepAlive": True,
        "ProcessType": "Background", "ThrottleInterval": 10, "ExitTimeOut": 15,
        "EnvironmentVariables": {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
                                 "TADO_WEB_DIST": str(root / "dist"), "TADO_ALLOWED_HOSTS": hostname,
                                 "CASA_INVOICES": str(root / "verified-invoices.json")},
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
    }
    path = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(plistlib.dumps(plist))
    path.chmod(0o600)
    subprocess.run(["/usr/bin/plutil", "-lint", str(path)], check=True)
    subprocess.run(["/bin/launchctl", "enable", domain + "/" + LABEL], check=True)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(path)], check=True)
    print("Installed:", path)
    print("Local: http://127.0.0.1:8765")
    if hostname:
        print("Tailscale hostname:", hostname)
    print("Does not inhibit system sleep. Available while this Mac is awake, including screen lock.")


if __name__ == "__main__":
    main()
