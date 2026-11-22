#!/usr/bin/env python3
"""Prepare a reviewable administrator installer; does not elevate itself."""
from pathlib import Path
import plistlib
import shlex

def main():
    source = Path(__file__).resolve().parent
    stage = source.parents[1] / ".install"
    stage.mkdir(exist_ok=True)
    label = "local.tado-history.wake"
    helper = "/Library/PrivilegedHelperTools/local.tado-history.wake.sh"
    target = "/Library/LaunchDaemons/" + label + ".plist"
    plist = {
        "Label": label,
        "ProgramArguments": ["/bin/sh", helper],
        "RunAtLoad": True,
        "StartCalendarInterval": [{"Hour": 0, "Minute": 10}, {"Hour": 12, "Minute": 0}, {"Hour": 23, "Minute": 50}],
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "ProcessType": "Background",
    }
    plist_path = stage / (label + ".plist")
    plist_path.write_bytes(plistlib.dumps(plist))
    installer = stage / "install_wake.sh"
    installer.write_text("\n".join([
        "#!/bin/sh", "set -eu",
        "/usr/bin/install -d -o root -g wheel -m 755 /Library/PrivilegedHelperTools",
        f"/usr/bin/install -o root -g wheel -m 755 {shlex.quote(str(source / 'wake_helper.sh'))} {shlex.quote(helper)}",
        f"/usr/bin/install -o root -g wheel -m 644 {shlex.quote(str(plist_path))} {shlex.quote(target)}",
        f"/usr/bin/plutil -lint {shlex.quote(target)}",
        f"/bin/launchctl bootout system/{label} 2>/dev/null || true",
        f"/bin/launchctl bootstrap system {shlex.quote(target)}",
        "",
    ]))
    installer.chmod(0o700)
    print(installer)


if __name__ == "__main__":
    main()
