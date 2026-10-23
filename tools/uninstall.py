#!/usr/bin/env python3
"""Remove claude-display's hooks/status line from ~/.claude/settings.json and stop the agent.

Removes only entries this project added (matched by script path), so settings changed since
the install are kept. --dry-run prints the result and compares it with the pre-install backup.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from install import LABEL, PLIST, SETTINGS, STATE, unmerged, write_json_atomic  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-agent", action="store_true")
    a = ap.parse_args()

    current = json.loads(SETTINGS.read_text())
    restored = unmerged(current)
    backup = json.loads(STATE.read_text())["backup"] if STATE.exists() else None
    if backup and os.path.exists(backup):
        same = json.loads(open(backup).read()) == restored
        print(f"restored settings {'==' if same else '!='} pre-install backup ({backup})")
        if not same:
            before = json.loads(open(backup).read())
            for k in sorted(set(before) | set(restored)):
                if before.get(k) != restored.get(k):
                    print(f"  differs: {k} (changed since install, kept)")
    if a.dry_run:
        return 0
    write_json_atomic(SETTINGS, restored)
    print("settings.json: claude-display hooks and statusLine removed")
    if not a.keep_agent:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
        if PLIST.exists():
            PLIST.unlink()
        print("LaunchAgent removed")
        state = Path.home() / "Library" / "Application Support" / "claude-display"
        if state.exists():
            shutil.rmtree(state)
            print("saved session state removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
