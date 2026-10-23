#!/usr/bin/env python3
"""Install claude-display: Claude Code hooks + status line, and the bridge LaunchAgent.

Only the `hooks` and `statusLine` keys of ~/.claude/settings.json are touched, after a
timestamped backup; the file is replaced atomically. Re-running is idempotent.

  python3 tools/install.py [--dry-run] [--no-agent]
  python3 tools/uninstall.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = Path.home() / ".claude" / "settings.json"
EMIT = str(ROOT / "hooks" / "emit.sh")
STATUSLINE = str(ROOT / "hooks" / "statusline.sh")
LABEL = "com.github.matsvandamme.esp-claude"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / "Library" / "Logs" / "claude-display.log"
STATE = ROOT / "secrets" / "install-state.json"  # which backup belongs to this install

# Events the bridge understands. Tool events take a matcher; the rest don't.
TOOL_EVENTS = ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"]
OTHER_EVENTS = [
    "SessionStart", "SessionEnd", "UserPromptSubmit", "Notification", "Stop", "StopFailure",
    "SubagentStart", "SubagentStop", "PreCompact", "PostCompact", "Elicitation", "ElicitationResult",
    "PostModelSwitch",
]


def is_ours(entry: dict) -> bool:
    return any(h.get("command") == EMIT for h in entry.get("hooks", []) if isinstance(h, dict))


def merged(settings: dict) -> dict:
    out = json.loads(json.dumps(settings))
    hooks = out.setdefault("hooks", {})
    for ev in TOOL_EVENTS + OTHER_EVENTS:
        entries = [e for e in hooks.get(ev, []) if not is_ours(e)]
        mine = {"hooks": [{"type": "command", "command": EMIT, "timeout": 2}]}
        if ev in TOOL_EVENTS:
            mine = {"matcher": "", **mine}
        hooks[ev] = entries + [mine]
    sl = out.get("statusLine")
    if sl and sl.get("command") not in (None, STATUSLINE):
        raise SystemExit(f"refusing to replace an existing statusLine: {sl.get('command')}")
    out["statusLine"] = {"type": "command", "command": STATUSLINE, "refreshInterval": 5}
    return out


def unmerged(settings: dict) -> dict:
    out = json.loads(json.dumps(settings))
    hooks = out.get("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [e for e in hooks[ev] if not is_ours(e)]
        if not hooks[ev]:
            del hooks[ev]
    if "hooks" in out and not out["hooks"]:
        del out["hooks"]
    if (out.get("statusLine") or {}).get("command") == STATUSLINE:
        del out["statusLine"]
    return out


def write_json_atomic(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(tmp, path.stat().st_mode & 0o777 if path.exists() else 0o600)
    os.replace(tmp, path)


def plist_xml() -> str:
    py = ROOT / "bridge" / ".venv" / "bin" / "python"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array><string>{py}</string><string>-m</string><string>claude_display</string><string>serve</string></array>
  <key>WorkingDirectory</key><string>{ROOT / "bridge"}</string>
  <key>EnvironmentVariables</key><dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>5</integer>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>{LOG}</string>
  <key>StandardErrorPath</key><string>{LOG}</string>
</dict>
</plist>
"""


def launchctl(*args: str) -> int:
    return subprocess.run(["launchctl", *args], capture_output=True).returncode


def install_agent(dry: bool) -> None:
    print(f"LaunchAgent -> {PLIST}")
    if dry:
        return
    subprocess.run(["pkill", "-f", "claude_display serve"], capture_output=True)  # hand-started copies hold the port
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    launchctl("bootout", f"{domain}/{LABEL}")
    PLIST.write_text(plist_xml())
    if launchctl("bootstrap", domain, str(PLIST)) != 0:
        raise SystemExit("launchctl bootstrap failed")
    launchctl("kickstart", "-k", f"{domain}/{LABEL}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-agent", action="store_true")
    a = ap.parse_args()

    current = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    new = merged(current)
    if new == current:
        print("settings.json already up to date")
    else:
        added = sorted(set(new) - set(current)) + [k for k in ("hooks",) if k in current and new[k] != current[k]]
        print(f"settings.json: adding/updating {', '.join(sorted(set(added)))}")
        if not a.dry_run:
            backup = SETTINGS.with_name(f"settings.json.bak-claude-display-{time.strftime('%Y%m%d-%H%M%S')}")
            if SETTINGS.exists():
                shutil.copy2(SETTINGS, backup)
                print(f"backup -> {backup}")
            STATE.parent.mkdir(mode=0o700, exist_ok=True)
            if not STATE.exists():
                STATE.write_text(json.dumps({"backup": str(backup)}))
            write_json_atomic(SETTINGS, new)
    if not a.no_agent:
        install_agent(a.dry_run)
    print("done. Hooks apply to Claude Code sessions started from now on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
