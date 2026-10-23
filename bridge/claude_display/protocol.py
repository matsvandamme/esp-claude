"""Wire format shared with the firmware (see firmware/main/snap.c): newline-delimited JSON."""
from __future__ import annotations

import json
import time

from .model import Model


def encode(msg: dict) -> bytes:
    return (json.dumps(msg, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def clock_fields(wall: float) -> dict:
    """Wall clock for the display (D46): UTC seconds and the local UTC offset."""
    return {"tm": int(wall), "tz": time.localtime(wall).tm_gmtoff}


def build_snapshot(model: Model, now: float, wall: float | None = None) -> dict:
    wall = model.wall() if wall is None else wall
    order = model.ordered()
    focus = model.focus(now)
    snap: dict = {
        "t": "snap",
        "f": focus.snapshot_focus(now) if focus else None,
        "s": [s.state for s in order],
        "fi": next((i for i, s in enumerate(order) if focus and s.sid == focus.sid), -1),
    }
    if focus and focus.state == "limit":
        reset = model.limit_reset(wall)
        snap["f"]["lr"] = int(reset - wall) if reset is not None else -1
    alert = model.other_alert(now, focus)
    if alert:
        snap["ob"] = alert
    done = model.done_elsewhere(focus)
    if done:
        snap["od"] = done
    # D51: every session for the overview: label, state, seconds in it, colour, flags (bit 0 done-away).
    snap["ss"] = [[s.label[:27], s.state, max(0, int(now - s.state_since)), s.color, int(s.done_away)] for s in order[:8]]
    t = model.today
    t.roll(wall)
    snap["td"] = {"n": t.turns, "a": int(t.active_s), "k": t.tokens}
    snap.update(clock_fields(wall))
    m: dict = {}
    if focus:
        m["ctx"] = focus.ctx_pct if focus.ctx_pct is not None else -1
        if focus.ctx_pct is not None:
            m["ctk"] = focus.ctx_tok
            m["cw"] = focus.ctx_win
    # Plan limits are account-wide: shown even with no session in focus. No cost: on a
    # subscription the API-equivalent dollar figure is misleading (D33).
    m.update(model.rates(wall))
    if m:
        snap["m"] = m
    return snap


def snapshot_key(snap: dict) -> str:
    """Identity of a snapshot for change detection; drops fields that tick on their own."""
    f = snap.get("f")
    if f:
        f = {k: v for k, v in f.items() if k not in ("el", "ws", "lr", "ta", "da")}
    m = {k: v for k, v in (snap.get("m") or {}).items() if k not in ("h5r", "d7r")}
    ss = [e[:2] + e[3:] for e in snap.get("ss", [])]
    td = {k: v for k, v in (snap.get("td") or {}).items() if k != "a"}
    rest = {k: v for k, v in snap.items() if k not in ("tm", "tz")}
    return json.dumps({**rest, "f": f, "m": m, "ss": ss, "td": td}, sort_keys=True)
