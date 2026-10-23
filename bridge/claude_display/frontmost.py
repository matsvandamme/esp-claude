"""Which Ghostty terminal is focused (decision D29).

One long-lived `osascript` loop reports the focused terminal of Ghostty's front window a few
times per second. Ghostty's scripting dictionary has no tty, so sessions are matched by the
terminal's working directory (see Model.set_window). `tell application` is guarded by
`is running` so the watcher never launches Ghostty.
"""
from __future__ import annotations

import logging
import subprocess
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("claude_display.frontmost")

POLL_S = 0.3

SCRIPT = f"""
-- "tab" means Ghostty's tab class inside the tell block, so use a variable for the separator.
set sep to character id 9
repeat
    set out to "0"
    try
        if application "Ghostty" is running then
            tell application "Ghostty"
                if frontmost then
                    set t to focused terminal of selected tab of front window
                    set out to "1" & sep & (id of t) & sep & (working directory of t) & sep & (name of t)
                end if
            end tell
        end if
    on error errMsg number errNum
        set out to "E" & sep & errNum & sep & errMsg
    end try
    log out
    delay {POLL_S}
end repeat
"""


@dataclass(frozen=True)
class Window:
    frontmost: bool          # a Ghostty window is the active app
    terminal_id: str = ""
    cwd: str = ""
    title: str = ""


def parse(line: str) -> Window | str | None:
    """A Window, an error string ("E…"), or None for junk."""
    parts = line.rstrip("\n").split("\t")
    if parts[0] == "0":
        return Window(False)
    if parts[0] == "1" and len(parts) >= 3:
        return Window(True, parts[1], parts[2], parts[3] if len(parts) > 3 else "")
    if parts[0] == "E":
        return "\t".join(parts[1:])
    return None


class GhosttyWatcher:
    def __init__(self, on_change: Callable[[Window], None]):
        self._on_change = on_change
        self.current: Window | None = None
        self.error = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ghostty-watch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def summary(self) -> dict:
        w = self.current
        return {"frontmost": w.frontmost if w else None, "cwd": w.cwd if w else None, "error": self.error or None}

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                proc = subprocess.Popen(["osascript", "-e", SCRIPT], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            except OSError as e:
                self.error = str(e)
                self._stop.wait(10)
                continue
            assert proc.stderr
            for line in proc.stderr:  # AppleScript `log` writes to stderr
                if self._stop.is_set():
                    break
                r = parse(line)
                if isinstance(r, str):
                    if r != self.error:
                        log.warning("ghostty watcher: %s", r)
                    self.error = r
                elif r is not None:
                    self.error = ""
                    # The title carries Claude's spinner glyph; only real focus changes matter.
                    key = (r.frontmost, r.terminal_id, r.cwd)
                    cur = self.current
                    if cur is None or key != (cur.frontmost, cur.terminal_id, cur.cwd):
                        self.current = r
                        self._on_change(r)
            proc.kill()
            self._stop.wait(2)
