#!/usr/bin/env python3
"""Install the built prototype as a user service, separately from the collector."""
from html import escape
import json
import os
from pathlib import Path
import plistlib
import shutil
from struct import unpack
import subprocess
import sys
import time

LABEL = "local.tado-history.web"
ROOT = Path.home() / "Library/Application Support/TadoWeb"
PROJECT = Path(__file__).resolve().parents[2]
TAILSCALE = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def local_branding(index, root):
    config = root / "branding.json"
    if config.exists():
        try:
            settings = json.loads(config.read_text(encoding="utf-8"))
            name = settings.get("name") if isinstance(settings, dict) else None
            if not isinstance(name, str) or not name.strip():
                raise ValueError
        except (OSError, ValueError) as error:
            raise SystemExit(f"Expected a JSON object with a non-empty name in {config}") from error
        name = escape(name.strip(), quote=True)
        default_title = "<title>Casa · Heating</title>"
        default_ios_title = '<meta name="apple-mobile-web-app-title" content="Casa" />'
        if default_title not in index or default_ios_title not in index:
            raise SystemExit("Build web before applying local branding")
        index = index.replace(default_title, f"<title>{name}</title>", 1)
        index = index.replace(default_ios_title, f'<meta name="apple-mobile-web-app-title" content="{name}" />', 1)

    icon = root / "apple-touch-icon.png"
    if not icon.is_file():
        return index, None
    with icon.open("rb") as asset:
        header = asset.read(24)
    if (len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR" or unpack(">II", header[16:]) != (180, 180)):
        raise SystemExit(f"Expected a 180 × 180 PNG in {icon}")
    default_icon = '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />'
    if default_icon not in index:
        raise SystemExit("Build web before applying the local icon")
    icon_links = (
        '<link rel="icon" type="image/png" sizes="180x180" href="/apple-touch-icon.png" />\n'
        '    <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />'
    )
    return index.replace(default_icon, icon_links, 1), icon


def main():
    os.umask(0o077)
    built_index = PROJECT / "web/dist/index.html"
    if not built_index.is_file():
        raise SystemExit("Build first: npm ci --prefix web && npm run build --prefix web")
    root = ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    index, icon = local_branding(built_index.read_text(encoding="utf-8"), root)
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
    package = root / "backend"
    package.mkdir(exist_ok=True)
    for name in ("__init__.py", "web_server.py", "refresh_coordinator.py", "environment_history.py", "heating_sessions.py",
                 "window_detection.py", "window_store.py", "provider_refresh.py", "octopus_sync.py", "octopus_api.py", "tado_history.py"):
        shutil.copy2(PROJECT / "backend" / name, package / name)
    verified = PROJECT / "output/octopus/verified-invoices.json"
    if verified.exists():
        shutil.copy2(verified, root / "verified-invoices.json")
        (root / "verified-invoices.json").chmod(0o600)
    # Replace only this application's static build; history and collector are separate.
    shutil.rmtree(root / "dist", ignore_errors=True)
    shutil.copytree(PROJECT / "web/dist", root / "dist")
    (root / "dist/index.html").write_text(index, encoding="utf-8")
    if icon is not None:
        shutil.copy2(icon, root / "dist/apple-touch-icon.png")
    hostname = ""
    if Path(TAILSCALE).exists():
        result = subprocess.run([TAILSCALE, "status", "--json"], capture_output=True, text=True)
        if result.returncode == 0:
            hostname = json.loads(result.stdout).get("Self", {}).get("DNSName", "").rstrip(".")
    plist = {
        "Label": LABEL,
        "ProgramArguments": [str(env / "bin/python"), "-m", "backend.web_server"],
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
