"""Synthetic Claude Code activity, posted to a running bridge exactly like the real hooks.

Three fake sessions loop through every state (thinking, tools, parallel tools, subagents,
permission prompts, questions, compaction, errors, done, idle) with plausible timing.
Used for the soak test and for latency measurements.
"""
from __future__ import annotations

import json
import random
import time
import urllib.request

URL = "http://127.0.0.1:47321"

PROJECTS = ["/Users/demo/Projects/esp-claude", "/Users/demo/Projects/trading-bot", "/Users/demo/Projects/website"]
TOOLS = [
    ("Bash", {"command": "npm test"}),
    ("Read", {"file_path": "src/app.ts"}),
    ("Edit", {"file_path": "src/ui/panel.tsx"}),
    ("Grep", {"pattern": "useEffect"}),
    ("Bash", {"command": "idf.py build"}),
    ("Glob", {"pattern": "**/*.py"}),
    ("WebFetch", {"url": "https://docs.example.com/api?key=abc"}),
]


def post(path: str, payload: dict) -> str:
    req = urllib.request.Request(URL + path, data=json.dumps(payload).encode(), method="POST")
    with urllib.request.urlopen(req, timeout=2) as r:
        return r.read().decode()


class FakeSession:
    def __init__(self, n: int, rng: random.Random):
        self.sid = f"demo{n:04d}-{rng.randrange(16**8):08x}"
        self.cwd = PROJECTS[n % len(PROJECTS)]
        self.rng = rng
        self.uid = 0
        self.ctx = rng.randint(5, 30)
        self.cost = 0.0

    def hook(self, ev: str, **kw) -> None:
        post("/hook", {"hook_event_name": ev, "session_id": self.sid, "cwd": self.cwd, **kw})

    def status(self) -> None:
        self.ctx = min(99, self.ctx + self.rng.randint(0, 3))
        self.cost += self.rng.random() * 0.05
        win = 200000
        post("/status", {
            "session_id": self.sid, "cwd": self.cwd,
            "model": {"id": self.rng.choice(["claude-opus-5-5", "claude-sonnet-5-5"]), "display_name": "Opus"},
            "cost": {"total_cost_usd": self.cost},
            "context_window": {"context_window_size": win, "used_percentage": self.ctx,
                               "current_usage": {"input_tokens": win * self.ctx // 100}},
            "rate_limits": {"five_hour": {"used_percentage": 37.0}, "seven_day": {"used_percentage": 52.0}},
        })

    def tool(self, sleep, name=None, ti=None, fail=False, permission=False, wait=2.0) -> None:
        if name is None:
            name, ti = self.rng.choice(TOOLS)
        self.uid += 1
        uid = f"toolu_{self.sid}_{self.uid}"
        self.hook("PreToolUse", tool_name=name, tool_input=ti, tool_use_id=uid)
        if permission:
            self.hook("PermissionRequest", tool_name=name, tool_input=ti)
            sleep(wait)
        sleep(self.rng.uniform(0.2, 1.5))
        self.hook("PostToolUseFailure" if fail else "PostToolUse", tool_name=name, tool_use_id=uid)

    def turn(self, sleep) -> None:
        r = self.rng
        self.hook("UserPromptSubmit", prompt="(demo)")
        self.status()
        sleep(r.uniform(0.5, 2))
        for _ in range(r.randint(2, 6)):
            roll = r.random()
            if roll < 0.12:
                self.tool(sleep, "Bash", {"command": "git push origin main"}, permission=True, wait=r.uniform(2, 6))
            elif roll < 0.18:
                self.tool(sleep, "AskUserQuestion", {"questions": [{"header": "Layout"}]}, wait=0)
            elif roll < 0.25:
                self.hook("SubagentStart", agent_id=f"ag{self.uid}", agent_type="Explore")
                self.tool(sleep, "Agent", {"description": "Explore the codebase"})
                self.hook("SubagentStop", agent_id=f"ag{self.uid}")
            elif roll < 0.30:
                self.hook("PreCompact", trigger="auto")
                sleep(r.uniform(1, 3))
                self.hook("PostCompact", trigger="auto")
                self.ctx = 20
            else:
                self.tool(sleep, fail=r.random() < 0.1)
            self.status()
            sleep(r.uniform(0.2, 1.0))
        if r.random() < 0.05:
            self.hook("StopFailure", error="overloaded")
        else:
            self.hook("Stop")
        self.status()


def run(minutes: float, sessions: int = 3, seed: int | None = None) -> int:
    import threading

    rng = random.Random(seed)
    end = time.monotonic() + minutes * 60
    stop = threading.Event()
    turns = [0]

    def worker(n: int) -> None:
        s = FakeSession(n, random.Random(rng.random()))
        s.hook("SessionStart", source="startup", model="claude-opus-5-5")
        while time.monotonic() < end and not stop.is_set():
            s.turn(stop.wait)
            turns[0] += 1
            stop.wait(s.rng.uniform(1, 4))
        s.hook("SessionEnd", reason="other")

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(sessions)]
    for t in threads:
        t.start()
        time.sleep(0.7)
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        stop.set()
    return turns[0]
