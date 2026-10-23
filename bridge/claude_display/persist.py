"""Session state on disk so a bridge restart doesn't blank the display (D36).

Only what the model already holds is written: project names, states, masked tool
targets and numbers. Prompt and reply text never enters the model (D20), so it can't
end up here. Times in the model are monotonic; on disk they are wall-clock seconds.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from .model import Model, Session, Tool

log = logging.getLogger("claude_display.persist")

DEFAULT_PATH = Path.home() / "Library" / "Application Support" / "claude-display" / "state.json"
VERSION = 1

_TIMES = ("created", "state_since", "last_activity", "turn_start", "wait_since", "done_at")
_PLAIN = ("project", "model", "state", "verb", "turn_elapsed", "ntools", "reason", "pre_compact_state",
          "transcript_path", "cwd", "ctx_pct", "ctx_tok", "ctx_win", "cost", "limit_until", "name", "color",
          "done_away")


def _tool(t: Tool, w) -> list:
    return [t.name, t.target, t.status, t.use_id, w(t.started)]


def dump(model: Model, mono: float, wall: float) -> dict:
    off = wall - mono

    def w(t: float) -> float | None:
        return t + off if t else None

    sessions = []
    for s in model.sessions.values():
        d = {"sid": s.sid, **{k: getattr(s, k) for k in _PLAIN}, **{k: w(getattr(s, k)) for k in _TIMES}}
        d["recent"] = [_tool(t, w) for t in s.recent]
        d["in_flight"] = [_tool(t, w) for t in s.in_flight.values()]
        d["subagents"] = sorted(s.subagents)
        sessions.append(d)
    return {
        "v": VERSION,
        "saved": wall,
        "sessions": sessions,
        "rates": {k: getattr(model, k) for k in ("rate_5h", "rate_7d", "rate_5h_reset", "rate_7d_reset")},
        "window_cwd": model.window_cwd,
        "window_sid": model.window_sid,
        "today": {"day": model.today.day, "turns": model.today.turns, "active_s": model.today.active_s,
                  "tokens": model.today.tokens},
    }


def restore(model: Model, data: dict, mono: float, wall: float) -> None:
    if data.get("v") != VERSION:
        return
    off = wall - mono

    def m(t) -> float:
        return float(t) - off if t else 0.0

    for d in data.get("sessions", []):
        s = Session(sid=str(d["sid"]))
        for k in _PLAIN:
            if k in d:
                setattr(s, k, d[k])
        for k in _TIMES:
            setattr(s, k, m(d.get(k)))
        # The in-flight set ties a running tool to its PostToolUse; recent shares the objects.
        def tool(t: list) -> Tool:  # [name, target, status, use_id, started]; older files lack started
            return Tool(*t[:4], m(t[4]) if len(t) > 4 else 0.0)

        flight = {t[3]: tool(t) for t in d.get("in_flight", [])}
        s.in_flight = flight
        s.recent = [flight.get(t[3]) or tool(t) for t in d.get("recent", [])]
        s.subagents = set(d.get("subagents", []))
        model.sessions[s.sid] = s
    for k, v in (data.get("rates") or {}).items():
        if k in ("rate_5h", "rate_7d", "rate_5h_reset", "rate_7d_reset") and v is not None:
            setattr(model, k, float(v))
    model.window_cwd = data.get("window_cwd")
    model.window_sid = data.get("window_sid")
    td = data.get("today") or {}
    if isinstance(td, dict) and td.get("day"):
        model.today.day = str(td["day"])
        model.today.turns = int(td.get("turns", 0))
        model.today.active_s = float(td.get("active_s", 0))
        model.today.tokens = int(td.get("tokens", 0))
    # Stale guards and the silent-session drop apply to the time the bridge was down.
    model.tick(mono)
    model._touch()


class Store:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.saved_version = -1

    def load(self, model: Model, mono: float, wall: float | None = None) -> bool:
        try:
            data = json.loads(self.path.read_text())
            restore(model, data, mono, time.time() if wall is None else wall)
        except FileNotFoundError:
            return False
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            log.warning("ignoring unreadable state file: %s", type(e).__name__)
            model.sessions.clear()
            return False
        self.saved_version = model.version
        log.info("restored %d session(s)", len(model.sessions))
        return True

    def save(self, model: Model, mono: float, wall: float | None = None) -> None:
        data = dump(model, mono, time.time() if wall is None else wall)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, separators=(",", ":"))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            log.warning("could not save state", exc_info=True)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return
        self.saved_version = model.version
