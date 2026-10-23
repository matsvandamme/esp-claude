"""Per-session state for every Claude Code session on this Mac.

Inputs: hook payloads (on_hook), status-line JSON (on_status) and transcript findings
(on_transcript_*). Output: a snapshot dict for the display (see protocol.build_snapshot).
All methods take an explicit `now` (seconds, monotonic) so tests can drive time.

Nothing from a payload is stored except what the display needs: tool name plus a masked
short target, project basename, model name, and numbers (decisions D3, D20).
"""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .mask import tool_display_name, tool_target, truncate

WORKING = {"start", "think", "tool", "compact"}
NEEDS_USER = {"perm", "ask"}
PRIORITY = {"perm": 6, "ask": 6, "limit": 5, "error": 5, "tool": 4, "think": 4, "compact": 4, "start": 3, "done": 2, "idle": 1}

STALE_WORKING_S = 10 * 60      # working with no sign of life -> idle
DROP_SILENT_S = 30 * 60        # forget sessions that went silent (no SessionEnd, e.g. a crash)
DROP_SILENT_NEEDS_S = 2 * 3600  # ...but keep a pending question around longer
ERROR_HOLD_S = 90              # an error outranks working sessions only this long
LIMIT_FULL_PCT = 99
RESET_SLACK_S = 60             # resets_at jitters by a few seconds between API replies
DONE_AWAY_MIN_S = 60           # a turn this long that ends while you look elsewhere stays flagged (D49)
LONG_TOOL_EXEMPT = {"Agent", "Task"}  # subagents legitimately run for many minutes (D50)
ACTIVE_TICK_MAX_S = 2.0        # longer gaps between ticks (sleep, bridge down) don't count as active

# A selection of Claude Code's spinner verbs.
VERBS = [
    "Accomplishing", "Architecting", "Baking", "Beaming", "Booping", "Brewing", "Calculating", "Cerebrating",
    "Channelling", "Churning", "Clauding", "Cogitating", "Combobulating", "Computing", "Concocting", "Conjuring",
    "Considering", "Cooking", "Crafting", "Creating", "Crunching", "Deciphering", "Deliberating", "Determining",
    "Doing", "Effecting", "Elucidating", "Enchanting", "Envisioning", "Finagling", "Forging", "Generating",
    "Hatching", "Herding", "Honking", "Hustling", "Ideating", "Imagining", "Incubating", "Inferring", "Manifesting",
    "Marinating", "Moseying", "Mulling", "Musing", "Noodling", "Percolating", "Pondering", "Processing",
    "Puttering", "Reticulating", "Ruminating", "Schlepping", "Shimmying", "Simmering", "Spinning", "Stewing",
    "Synthesizing", "Thinking", "Tinkering", "Transmuting", "Unfurling", "Vibing", "Wandering", "Whirring",
    "Wibbling", "Working", "Wrangling", "Zesting",
]

_MODEL_RE = re.compile(r"claude-(opus|sonnet|haiku|fable)-(\d+)(?:-(\d+))?", re.I)


def model_label(raw: str | None) -> str:
    """'claude-opus-5-5' / 'Opus 5.5' / {'display_name': ...} -> 'Opus 5.5'."""
    if not raw:
        return ""
    m = _MODEL_RE.search(raw)
    if m:
        ver = m.group(2) + (f".{m.group(3)}" if m.group(3) and len(m.group(3)) < 3 else "")
        return f"{m.group(1).capitalize()} {ver}"
    return truncate(raw, 18)


@dataclass
class Tool:
    name: str
    target: str
    status: int = 1  # 0 done, 1 running, 2 failed
    use_id: str = ""
    started: float = 0.0  # when it started running (or was approved), monotonic


@dataclass
class Session:
    sid: str
    project: str = ""
    model: str = ""
    created: float = 0.0
    state: str = "idle"
    state_since: float = 0.0
    last_activity: float = 0.0
    verb: str = "Thinking"
    turn_start: float = 0.0
    turn_elapsed: float = 0.0
    ntools: int = 0
    in_flight: dict[str, Tool] = field(default_factory=dict)
    recent: list[Tool] = field(default_factory=list)  # most recent first, max 2
    subagents: set[str] = field(default_factory=set)
    reason: str = ""
    wait_since: float = 0.0
    pre_compact_state: str = ""
    transcript_path: str = ""
    cwd: str = ""
    # status-line metrics
    ctx_pct: int | None = None
    ctx_tok: int = 0
    ctx_win: int = 0
    cost: float | None = None
    limit_until: float = 0.0  # wall-clock reset of the limit this session hit (D35)
    name: str = ""  # set by the user with /name (status line `session_name`), D40
    color: int = -1  # set by the user with /color (transcript `agent-color`), 0xRRGGBB, D41
    done_away: bool = False  # finished a long turn while the user looked elsewhere (D49)
    done_at: float = 0.0

    @property
    def label(self) -> str:
        return self.name or self.project

    def set_state(self, st: str, now: float) -> None:
        if st != self.state:
            if self.state in NEEDS_USER and st not in NEEDS_USER:
                for t in self.in_flight.values():
                    t.started = now  # waiting for approval isn't running time (D50)
            self.state = st
            self.state_since = now

    def tool_age(self, now: float) -> int | None:
        """How long the oldest running tool has been at it, while the session runs tools."""
        if self.state != "tool":
            return None
        starts = [t.started for t in self.in_flight.values() if t.started and t.name not in LONG_TOOL_EXEMPT]
        return int(now - min(starts)) if starts else None

    def snapshot_focus(self, now: float) -> dict:
        if self.state in WORKING:
            el = int(now - self.turn_start)
        elif self.state == "done":
            el = int(self.turn_elapsed)
        else:
            el = int(now - self.turn_start) if self.turn_start else 0
        return {
            "id": self.sid[:4],
            "p": self.label,
            "m": self.model,
            "st": self.state,
            "v": self.verb,
            "el": max(0, el),
            "nt": self.ntools,
            "sub": len(self.subagents),
            "tl": [[t.name, t.target, t.status] for t in self.recent[:2]],
            "w": self.reason if self.state in NEEDS_USER or self.state == "error" else "",
            "ws": int(now - self.wait_since) if self.state in NEEDS_USER else 0,
            **({"c": self.color} if self.color >= 0 else {}),
            **({"ta": ta} if (ta := self.tool_age(now)) is not None else {}),
            **({"da": int(now - self.done_at)} if self.done_away else {}),
        }


@dataclass
class Today:
    """Counters for the local calendar day (D52): turns started, time with at least one
    session working, and new tokens (input + output + cache creation, no cache reads)."""
    day: str = ""
    turns: int = 0
    active_s: float = 0.0
    tokens: int = 0
    seen: dict[str, int] = field(default_factory=dict)  # message id -> tokens counted

    def roll(self, wall: float) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(wall))
        if day != self.day:
            self.day, self.turns, self.active_s, self.tokens = day, 0, 0.0, 0
            self.seen.clear()

    def add_usage(self, msg_id: str, tokens: int) -> bool:
        """Streamed replies repeat a message on several lines: count each id once, at its max."""
        prev = self.seen.get(msg_id, 0)
        if tokens <= prev:
            return False
        self.seen[msg_id] = tokens
        self.tokens += tokens - prev
        return True


class Model:
    def __init__(self, rng: random.Random | None = None, wall: Callable[[], float] = time.time):
        self.sessions: dict[str, Session] = {}
        self.wall = wall
        self.today = Today()
        self._last_tick: float | None = None
        self.rate_5h: float | None = None
        self.rate_7d: float | None = None
        self.rate_5h_reset: float | None = None  # epoch seconds
        self.rate_7d_reset: float | None = None
        self.window_cwd: str | None = None  # cwd of the focused Ghostty terminal (D29)
        self.window_sid: str | None = None  # last session seen in a focused window (D30)
        self._window_key: tuple[str, str] | None = None  # (terminal id, cwd) last focused
        self._window_front = False  # a Ghostty terminal is the active window right now
        self.pinned: str | None = None
        self._focus_sid: str | None = None
        self.version = 0  # bumps on every visible change
        self._rng = rng or random.Random()

    # ---------- helpers ----------

    def _touch(self) -> None:
        self.version += 1

    def _session(self, payload: dict, now: float) -> Session:
        sid = str(payload.get("session_id") or "unknown")
        s = self.sessions.get(sid)
        if s is None:
            s = Session(sid=sid, created=now, state_since=now, last_activity=now)
            self.sessions[sid] = s
        cwd = payload.get("cwd")
        if cwd and not s.project:
            s.cwd = cwd
            s.project = os.path.basename(cwd.rstrip("/")) or cwd
            self._dedupe_labels()
        if payload.get("transcript_path"):
            s.transcript_path = payload["transcript_path"]
        s.last_activity = now
        return s

    def _dedupe_labels(self) -> None:
        """D23: two sessions in the same directory get an id suffix."""
        by_base: dict[str, list[Session]] = {}
        for s in self.sessions.values():
            base = os.path.basename(s.cwd.rstrip("/")) if s.cwd else s.project.split("·")[0]
            by_base.setdefault(base, []).append(s)
        for base, group in by_base.items():
            for s in group:
                s.project = f"{base}·{s.sid[:4]}" if len(group) > 1 else base

    def _new_turn(self, s: Session, now: float) -> None:
        s.turn_start = now
        s.turn_elapsed = 0
        s.ntools = 0
        s.in_flight.clear()
        s.recent.clear()
        s.reason = ""
        s.done_away = False
        s.verb = self._rng.choice(VERBS)

    def _after_tool(self, s: Session, now: float) -> None:
        if s.state in NEEDS_USER and not s.in_flight:
            s.reason = ""
        if s.state in WORKING or s.state in NEEDS_USER:
            s.set_state("tool" if s.in_flight else "think", now)

    def _push_recent(self, s: Session, tool: Tool) -> None:
        s.recent.insert(0, tool)
        del s.recent[2:]

    # ---------- inputs ----------

    def on_hook(self, p: dict, now: float) -> None:
        ev = p.get("hook_event_name", "")
        if not p.get("session_id"):
            return
        if ev == "SessionEnd":
            if self.sessions.pop(str(p.get("session_id")), None):
                self._dedupe_labels()
                self._touch()
            return
        s = self._session(p, now)

        # D18: subagent activity only moves the subagent counter.
        agent_id = p.get("agent_id")
        if ev == "SubagentStart":
            if agent_id:
                s.subagents.add(str(agent_id))
            self._touch()
            return
        if ev == "SubagentStop":
            s.subagents.discard(str(agent_id))
            self._touch()
            return
        if agent_id and ev not in ("PermissionRequest", "Notification", "Elicitation"):
            return

        tool_name = str(p.get("tool_name", ""))
        if ev == "SessionStart":
            if p.get("model"):
                s.model = model_label(str(p.get("model")))
            if p.get("source") == "compact" and s.state == "compact":
                s.set_state(s.pre_compact_state or "think", now)
            elif s.state not in WORKING:
                s.set_state("idle", now)
        elif ev == "UserPromptSubmit":
            self._new_turn(s, now)
            self.today.roll(self.wall())
            self.today.turns += 1
            s.set_state("think", now)
        elif ev == "PreToolUse":
            uid = str(p.get("tool_use_id") or f"anon{now}")
            t = Tool(tool_display_name(tool_name), tool_target(tool_name, p.get("tool_input"), s.cwd), 1, uid, now)
            s.in_flight[uid] = t
            self._push_recent(s, t)
            s.ntools += 1
            if not s.turn_start:
                s.turn_start = now
            if tool_name == "AskUserQuestion":
                s.reason = "Claude has a question for you"
                s.wait_since = now
                s.set_state("ask", now)
            elif tool_name == "ExitPlanMode":
                s.reason = "Plan ready for your review"
                s.wait_since = now
                s.set_state("ask", now)
            elif s.state not in NEEDS_USER:
                s.set_state("tool", now)
        elif ev == "PermissionRequest":
            target = tool_target(tool_name, p.get("tool_input"), s.cwd)
            s.reason = truncate(f"{tool_display_name(tool_name)}: {target}" if target else tool_display_name(tool_name))
            if s.state not in NEEDS_USER:
                s.wait_since = now
            s.set_state("perm", now)
        elif ev in ("PostToolUse", "PostToolUseFailure"):
            uid = str(p.get("tool_use_id") or "")
            t = s.in_flight.pop(uid, None)
            status = 2 if ev == "PostToolUseFailure" else 0
            if t:
                t.status = status
            if tool_name in ("AskUserQuestion", "ExitPlanMode") or s.state in NEEDS_USER:
                s.reason = ""
                if s.state in NEEDS_USER:
                    s.set_state("tool" if s.in_flight else "think", now)
            self._after_tool(s, now)
        elif ev == "Notification":
            nt = p.get("notification_type", "")
            if nt == "permission_prompt" and s.state not in NEEDS_USER:
                s.reason = truncate(str(p.get("message") or "Permission needed"))
                s.wait_since = now - 6  # the notification fires ~6 s after the prompt appeared
                s.set_state("perm", now)
            elif nt in ("elicitation_dialog", "elicitation_url_dialog") and s.state not in NEEDS_USER:
                s.reason = truncate(str(p.get("message") or "An MCP server needs input"))
                s.wait_since = now - 6
                s.set_state("ask", now)
            elif nt == "idle_prompt" and s.state in ("done", "idle"):
                s.set_state("idle", now)
        elif ev == "Elicitation":
            s.reason = truncate(f"{p.get('mcp_server_name', 'MCP')} needs input")
            s.wait_since = now
            s.set_state("ask", now)
        elif ev == "ElicitationResult":
            s.reason = ""
            s.set_state("tool" if s.in_flight else "think", now)
        elif ev == "Stop":
            s.turn_elapsed = now - s.turn_start if s.turn_start else 0
            for t in s.in_flight.values():
                t.status = 0
            s.in_flight.clear()
            s.reason = ""
            s.set_state("done", now)
            looking = self._window_front and self._window_session() is s
            s.done_away = s.turn_elapsed >= DONE_AWAY_MIN_S and not looking
            s.done_at = now
        elif ev == "StopFailure":
            s.turn_elapsed = now - s.turn_start if s.turn_start else 0
            s.in_flight.clear()
            err = str(p.get("error") or "unknown")
            if err == "rate_limit":
                s.reason = ""
                s.set_state("limit", now)  # D35: a plan limit, not a fault
            else:
                s.reason = truncate(err.replace("_", " "))
                s.set_state("error", now)
        elif ev == "PreCompact":
            s.pre_compact_state = s.state if s.state in WORKING else "idle"
            s.set_state("compact", now)
        elif ev == "PostCompact":
            if s.state == "compact":
                s.set_state(s.pre_compact_state or "idle", now)
        elif ev == "PostModelSwitch":
            if p.get("to_model"):
                s.model = model_label(str(p.get("to_model")))
        else:
            return  # unknown/irrelevant events don't change the screen
        self._touch()

    def on_status(self, p: dict, now: float) -> None:
        if not p.get("session_id"):
            return
        s = self._session(p, now)
        name = p.get("session_name")
        s.name = truncate(name.strip(), 27) if isinstance(name, str) else ""
        model = p.get("model")
        if isinstance(model, dict):
            s.model = model_label(model.get("id") or model.get("display_name"))
        cw = p.get("context_window") or {}
        if isinstance(cw, dict):
            pct = cw.get("used_percentage")
            cu = cw.get("current_usage") or {}
            if isinstance(cu, dict) and cu:
                s.ctx_tok = int(cu.get("input_tokens", 0) or 0) + int(cu.get("cache_creation_input_tokens", 0) or 0) + int(
                    cu.get("cache_read_input_tokens", 0) or 0
                )
            s.ctx_win = int(cw.get("context_window_size") or s.ctx_win or 0)
            if pct is not None:
                s.ctx_pct = int(round(float(pct)))
            elif s.ctx_win and s.ctx_tok:
                s.ctx_pct = int(round(100 * s.ctx_tok / s.ctx_win))
        cost = (p.get("cost") or {}).get("total_cost_usd") if isinstance(p.get("cost"), dict) else None
        if cost is not None:
            s.cost = float(cost)
        rl = p.get("rate_limits") or {}
        if isinstance(rl, dict):
            for key, attr in (("five_hour", "rate_5h"), ("seven_day", "rate_7d")):
                w = rl.get(key)
                if isinstance(w, dict) and w.get("used_percentage") is not None:
                    reset = w.get("resets_at")
                    self._merge_rate(attr, float(w["used_percentage"]), float(reset) if reset is not None else None)
        self._touch()

    def _merge_rate(self, attr: str, used: float, reset: float | None) -> None:
        """D34: every session reports the limits from its own last API reply, so an idle
        session carries old numbers. Within one window usage only rises: keep the max.
        A later reset means a new window; an earlier one is stale and ignored."""
        cur_used = getattr(self, attr)
        cur_reset = getattr(self, attr + "_reset")
        if cur_used is None or reset is None or cur_reset is None or reset > cur_reset + RESET_SLACK_S:
            setattr(self, attr, used)
            if reset is not None:
                setattr(self, attr + "_reset", reset)
        elif reset >= cur_reset - RESET_SLACK_S:
            setattr(self, attr, max(cur_used, used))

    def rates(self, wall: float) -> dict:
        """Plan usage as shown: a window whose reset has passed counts as unused."""
        out: dict = {}
        for name, attr in (("h5", "rate_5h"), ("d7", "rate_7d")):
            used, reset = getattr(self, attr), getattr(self, attr + "_reset")
            if used is None:
                continue
            if reset is not None and reset <= wall:
                out[name] = 0
            else:
                out[name] = int(round(used))
                if reset is not None:
                    out[name + "r"] = int(reset - wall)
        return out

    def on_transcript_activity(self, sid: str, now: float) -> None:
        s = self.sessions.get(sid)
        if s:
            s.last_activity = now

    def on_transcript_usage(self, msg_id: str, tokens: int) -> None:
        self.today.roll(self.wall())
        if self.today.add_usage(msg_id, tokens):
            self._touch()

    def on_transcript_color(self, sid: str, color: int) -> None:
        s = self.sessions.get(sid)
        if s and s.color != color:
            s.color = color
            self._touch()

    def on_transcript_interrupt(self, sid: str, now: float, denied: bool = False) -> None:
        """D11: Esc or a denied permission ends the turn without any hook firing."""
        s = self.sessions.get(sid)
        if not s or s.state not in WORKING | NEEDS_USER:
            return
        s.turn_elapsed = now - s.turn_start if s.turn_start else 0
        for t in s.in_flight.values():
            t.status = 2 if denied else 0
        s.in_flight.clear()
        s.reason = ""
        s.set_state("idle", now)
        self._touch()

    def on_button(self, now: float) -> None:
        order = self.ordered()
        if len(order) < 2:
            return
        cur = self.focus(now)
        idx = next((i for i, s in enumerate(order) if cur and s.sid == cur.sid), -1)
        self.pinned = order[(idx + 1) % len(order)].sid
        self._touch()

    def limit_reset(self, wall: float) -> float | None:
        """When the limit a session hit lifts: the exhausted window (weekly first). None when
        neither known window is full, e.g. a model-specific weekly cap the status line omits."""
        for used, reset in ((self.rate_7d, self.rate_7d_reset), (self.rate_5h, self.rate_5h_reset)):
            if used is not None and used >= LIMIT_FULL_PCT and reset is not None and reset > wall:
                return reset
        return None

    def tick(self, now: float, wall: float | None = None) -> None:
        """Time-based transitions: stale guards and forgetting dead sessions."""
        changed = False
        wall = self.wall() if wall is None else wall
        self.today.roll(wall)
        dt = now - self._last_tick if self._last_tick is not None else 0.0
        self._last_tick = now
        if 0 < dt <= ACTIVE_TICK_MAX_S and any(s.state in WORKING for s in self.sessions.values()):
            self.today.active_s += dt
        for sid, s in list(self.sessions.items()):
            if s.state == "limit":
                r = self.limit_reset(wall)
                if r is not None:
                    s.limit_until = r
                elif s.limit_until and s.limit_until <= wall:
                    s.set_state("idle", now)  # the exhausted window has reset
                    changed = True
            silent = now - s.last_activity
            limit = DROP_SILENT_NEEDS_S if s.state in NEEDS_USER else DROP_SILENT_S
            if silent > limit:
                del self.sessions[sid]
                changed = True
            elif s.state in WORKING and silent > STALE_WORKING_S:
                s.in_flight.clear()
                s.set_state("idle", now)
                changed = True
        if self.pinned and self.pinned not in self.sessions:
            self.pinned = None
            changed = True
        if changed:
            self._dedupe_labels()
            self._touch()

    # ---------- outputs ----------

    def ordered(self) -> list[Session]:
        return sorted(self.sessions.values(), key=lambda s: (s.created, s.sid))

    def priority(self, s: Session, now: float) -> int:
        if s.state == "error" and now - s.state_since > ERROR_HOLD_S:
            return PRIORITY["done"]
        return PRIORITY.get(s.state, 0)

    def set_window(self, frontmost: bool, cwd: str | None, now: float, terminal: str = "") -> None:
        """Ghostty focus changed. Outside Ghostty (or in a tab without Claude) the last
        focused session stays on screen (D30). Selecting a terminal (another one, or coming
        back to Ghostty from another app) is a choice like a BOOT press: the latest of the two
        decides what is on screen (D43)."""
        was_front, self._window_front = self._window_front, bool(frontmost and cwd)
        if not self._window_front:
            return
        key = (terminal, cwd.rstrip("/"))
        if key != self._window_key or not was_front:
            self._window_key = key
            self.window_cwd = key[1]
            self.pinned = None
            win = self._window_session()
            if win:
                win.done_away = False  # you've looked at it (D49)
            self._touch()

    def _window_session(self) -> Session | None:
        cwd = self.window_cwd
        if cwd:
            exact = [s for s in self.sessions.values() if s.cwd and s.cwd.rstrip("/") == cwd]
            near = exact or [
                s for s in self.sessions.values()
                if s.cwd and (cwd.startswith(s.cwd.rstrip("/") + "/") or s.cwd.startswith(cwd + "/"))
            ]
            if near:
                keep = [s for s in near if s.sid == self.window_sid]
                self.window_sid = (keep[0] if keep else max(near, key=lambda s: s.last_activity)).sid
        return self.sessions.get(self.window_sid or "")

    def other_alert(self, now: float, focus: Session | None) -> dict | None:
        """A session other than the one on screen that is waiting for the user (D31)."""
        waiting = [s for s in self.sessions.values() if s.state in NEEDS_USER and (not focus or s.sid != focus.sid)]
        if not waiting:
            return None
        s = min(waiting, key=lambda s: s.wait_since)
        return {"p": s.label, "w": s.reason, "n": len(waiting)}

    def done_elsewhere(self, focus: Session | None) -> dict | None:
        """Sessions other than the one on screen that finished while you were away (D49)."""
        done = [s for s in self.sessions.values() if s.done_away and (not focus or s.sid != focus.sid)]
        if not done:
            return None
        s = max(done, key=lambda s: s.done_at)
        return {"p": s.label, "n": len(done)}

    def focus(self, now: float) -> Session | None:
        """The session last chosen with BOOT or by selecting its Ghostty window, whichever
        happened last (D43). Without either: highest priority, sticky, so busy sessions don't
        make the screen flip."""
        if not self.sessions:
            self._focus_sid = None
            return None
        if self.pinned in self.sessions:
            return self.sessions[self.pinned]
        win = self._window_session()
        if win is not None:
            self._focus_sid = win.sid
            return win
        top = max(self.priority(s, now) for s in self.sessions.values())
        cur = self.sessions.get(self._focus_sid or "")
        if cur is None or self.priority(cur, now) < top:
            cands = [s for s in self.sessions.values() if self.priority(s, now) == top]
            cur = max(cands, key=lambda s: (s.turn_start, s.state_since))
            self._focus_sid = cur.sid
        return cur
