#!/usr/bin/env python3
"""Install/update the user collector without requiring an administrator."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

from backend import tado_history as tado

LABEL = "local.tado-history.collector"


def main():
    os.umask(0o077)
    root = tado.ROOT
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = root / "bin"
    binary.mkdir(exist_ok=True, mode=0o700)
    shutil.copy2(Path(__file__).resolve().parents[2] / "backend/tado_history.py", binary / "tado_history.py")
    config = root / "config.json"
    if not config.exists():
        tado.atomic_json(config, tado.DEFAULTS)
    # Stable Homebrew link survives patch-version upgrades.
    python = "/opt/homebrew/bin/python3" if Path("/opt/homebrew/bin/python3").exists() else sys.executable
    wrapper = binary / "collect.sh"
    wrapper.write_text('''#!/bin/sh
# Python enforces a 180-second deadline. Keep idle sleep inhibited only while it runs.
exec /usr/bin/caffeinate -i "$1" "$2" collect
''')
    os.chmod(wrapper, 0o700)
    # No network requests on these checks unless a configured slot is due.
    calendar = [{"Minute": minute} for minute in (0, 5, 15, 30, 45, 55, 57, 59)]
    plist = {
        "Label": LABEL,
        "ProgramArguments": ["/bin/sh", str(wrapper), python, str(binary / "tado_history.py")],
        "StartCalendarInterval": calendar,
        "RunAtLoad": True,
        "ProcessType": "Background",
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "ThrottleInterval": 60,
        "ExitTimeOut": 15,
    }
    directory = Path.home() / "Library/LaunchAgents"
    directory.mkdir(exist_ok=True)
    target = directory / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    subprocess.run(["/bin/launchctl", "bootout", domain + "/" + LABEL], capture_output=True)
    target.write_bytes(plistlib.dumps(plist))
    os.chmod(target, 0o600)
    subprocess.run(["/usr/bin/plutil", "-lint", str(target)], check=True)
    subprocess.run(["/bin/launchctl", "enable", domain + "/" + LABEL], check=True)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(target)], check=True)
    print("Installed:", target)
    print("Data:", root / "history.sqlite3")


if __name__ == "__main__":
    main()
