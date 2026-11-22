"""Install the independent five-minute Hue/weather collector, without wake events."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

from backend.environment_history import ROOT, AUTH


def main():
    os.umask(0o077)
    if not AUTH.exists():
        raise SystemExit("Pair the Hue bridge before installing the collector")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    label = "local.tado-history.environment"
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain + "/" + label], capture_output=True)
    for _ in range(50):
        if subprocess.run(["launchctl", "print", domain + "/" + label], capture_output=True).returncode:
            break
        time.sleep(.2)
    else:
        raise SystemExit("The previous collector is still stopping")
    script = ROOT / "environment_history.py"
    shutil.copy2(Path(__file__).resolve().parents[2] / "backend/environment_history.py", script)
    path = Path.home() / "Library/LaunchAgents" / (label + ".plist")
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(plistlib.dumps({
        "Label": label, "ProgramArguments": [sys.executable, str(script)],
        "WorkingDirectory": str(ROOT), "RunAtLoad": True, "StartInterval": 300,
        "ProcessType": "Background", "LowPriorityIO": True, "Nice": 10,
        "EnvironmentVariables": {"PYTHONDONTWRITEBYTECODE": "1"},
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null",
    }))
    path.chmod(0o600)
    subprocess.run(["plutil", "-lint", str(path)], check=True)
    subprocess.run(["launchctl", "enable", domain + "/" + label], check=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    print("Installed: entrance sensor every 5 minutes; Open-Meteo every 3 hours while the Mac is awake")


if __name__ == "__main__":
    main()
