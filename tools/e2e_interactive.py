#!/usr/bin/env python3
"""End-to-end test against a real, interactive Claude Code session (decision D19).

Spawns `claude --model haiku` in a fresh temp dir under a pty, makes it run a Bash command
that needs permission, approves it, and checks the bridge state and display at each step.
The bridge's GET /state is the oracle (the TUI is never parsed beyond the trust prompt);
screenshots come from GET /shot.

  uv run --project bridge python tools/e2e_interactive.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pexpect

BRIDGE = "http://127.0.0.1:47321"
OUT = Path(__file__).resolve().parent.parent / "artifacts" / "e2e"


def state() -> dict:
    with urllib.request.urlopen(BRIDGE + "/state", timeout=3) as r:
        return json.load(r)


def shot(name: str) -> None:
    with urllib.request.urlopen(BRIDGE + "/shot", timeout=15) as r:
        (OUT / f"{name}.png").write_bytes(r.read())
    print(f"  screenshot {name}.png")


def session_for(project: str) -> dict | None:
    return next((s for s in state()["sessions"] if s["project"].startswith(project)), None)


_screen = bytearray()


def pump(child, seconds: float) -> bytes:
    """Drain the pty and answer the terminal queries the TUI waits on (DA1), like a real terminal."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            chunk = child.read_nonblocking(65536, timeout=0.2)
        except pexpect.TIMEOUT:
            continue
        except pexpect.EOF:
            break
        for _ in range(chunk.count(b"\x1b[c")):
            child.send("\x1b[?62;22c")
        _screen.extend(chunk)
        del _screen[:-20000]
    return bytes(_screen)


def wait_state(project: str, wanted: set[str], timeout: float, child) -> dict:
    end = time.monotonic() + timeout
    seen = []
    while time.monotonic() < end:
        pump(child, 0.3)
        s = session_for(project)
        if s and (not seen or seen[-1] != s["state"]):
            seen.append(s["state"])
        if s and s["state"] in wanted:
            return s
    raise TimeoutError(f"{project}: wanted {wanted}, saw {seen}")


def device_stats() -> dict:
    with urllib.request.urlopen(BRIDGE + "/stats", timeout=5) as r:
        return json.load(r)


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=0, help="leave the permission prompt unanswered this long")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    # Under this (already trusted) project, so Claude Code inherits trust and skips the dialog.
    work = Path(tempfile.mkdtemp(prefix="cd-e2e-", dir=OUT))
    project = work.name
    log = open(OUT / "claude-tui.log", "wb")
    env = {**os.environ, "TERM": "xterm-256color"}
    print(f"temp project {project}")
    child = pexpect.spawn("claude", ["--model", "haiku", "--permission-mode", "default"], cwd=str(work), env=env,
                          dimensions=(40, 140), logfile=log, timeout=60)
    ok = False
    try:
        # First run in a new folder asks whether to trust it. "No, exit" is preselected,
        # so move down to "Yes, I trust this folder" before confirming.
        end = time.monotonic() + 40
        while time.monotonic() < end and b"trust" not in pump(child, 0.5) and not session_for(project):
            pass
        if b"trust" in bytes(_screen) and not session_for(project):
            pump(child, 1)
            child.send("\x1b[B")
            pump(child, 0.5)
            child.send("\r")
        wait_state(project, {"idle", "start"}, 30, child)
        print("session registered (SessionStart hook)")
        pump(child, 3)
        child.send("Use the Bash tool to run exactly: touch e2e_marker.txt && ls -la . Then reply with one word: done")
        pump(child, 0.5)
        child.send("\r")
        s = wait_state(project, {"think", "tool", "perm"}, 60, child)
        print(f"working: {s['state']}")
        shot("1_working")
        s = wait_state(project, {"perm"}, 90, child)
        print("needs permission (PermissionRequest hook)")
        pump(child, 1.0)
        shot("2_needs_permission")
        led = device_stats().get("led")
        print(f"  device led={led}")
        assert led == "alert", f"LED should breathe orange while waiting, got {led}"
        if args.hold:
            pump(child, args.hold)
            shot("2b_escalated")
            st = device_stats()
            print(f"  after {args.hold:.0f}s: led={st.get('led')} bl={st.get('bl')}")
            sid = session_for(project)["id"]
            if state()["focus"] == sid:
                assert st.get("led") == "alert_urgent", "LED should escalate after 60 s"
            else:
                # D31: a session that is not in the focused Ghostty window stays a badge.
                print("  not focused: shown as a header badge, LED pulses without escalating")
                assert st.get("led") == "alert"
            assert session_for(project)["state"] == "perm"
        child.send("\r")  # "Yes" is preselected
        s = wait_state(project, {"done", "idle"}, 120, child)
        pump(child, 4)  # let the status line deliver ctx/cost
        s = session_for(project)
        print(f"finished: {s}")
        shot("3_done")
        assert (work / "e2e_marker.txt").exists(), "Bash command did not run"
        assert s["tools"] >= 1, "tool count"
        assert s["ctx_pct"] and s["ctx_pct"] > 0, "status line metrics (ctx) missing"
        assert s["cost"] is not None and s["cost"] > 0, "status line metrics (cost) missing"
        ok = True
    finally:
        if child.isalive():
            child.sendcontrol("c")
            time.sleep(0.5)
            child.sendcontrol("c")
        child.close(force=True)
        log.close()
        time.sleep(2)
        gone = session_for(project) is None
        print(f"session removed after exit: {gone}")
        shutil.rmtree(work, ignore_errors=True)
    print("E2E PASS" if ok else "E2E FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
