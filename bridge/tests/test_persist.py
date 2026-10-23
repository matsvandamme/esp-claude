import json
import random

from claude_display.model import Model
from claude_display.persist import Store, dump, restore
from claude_display.protocol import build_snapshot

CWD = "/Users/x/Projects/esp-claude"


def busy_model(now):
    md = Model(rng=random.Random(1))
    md.on_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": CWD, "prompt": "secret words"}, now)
    md.on_hook({"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": CWD, "tool_name": "Bash",
                "tool_use_id": "t1", "tool_input": {"command": "npm test"}}, now + 1)
    md.on_status({"session_id": "s1", "cwd": CWD, "context_window": {"used_percentage": 33},
                  "rate_limits": {"five_hour": {"used_percentage": 20, "resets_at": 50_000}}}, now + 2)
    md.set_window(True, CWD, now + 2)
    return md


def test_round_trip_across_a_restart(tmp_path):
    md = busy_model(100)
    store = Store(tmp_path / "state.json")
    store.save(md, mono=103, wall=10_003)
    assert "secret" not in (tmp_path / "state.json").read_text()

    # New process: monotonic clock restarted near 7, 4 s of wall time passed.
    md2 = Model()
    assert Store(tmp_path / "state.json").load(md2, mono=7, wall=10_007)
    a, b = build_snapshot(md, 107, wall=10_007), build_snapshot(md2, 7, wall=10_007)
    assert a["f"] == b["f"] and a["m"] == b["m"]
    assert b["f"]["st"] == "tool" and b["f"]["el"] == 7 and b["f"]["tl"][0][:2] == ["Bash", "npm test"]
    # The restored in-flight tool still finishes the turn properly.
    md2.on_hook({"hook_event_name": "PostToolUse", "session_id": "s1", "tool_use_id": "t1", "tool_name": "Bash"}, 8)
    assert md2.sessions["s1"].state == "think" and md2.sessions["s1"].recent[0].status == 0


def test_long_downtime_expires_sessions_and_windows():
    md = busy_model(100)
    data = json.loads(json.dumps(dump(md, 103, 10_003)))
    md2 = Model()
    restore(md2, data, mono=5, wall=10_003 + 11 * 60)   # 11 min later: working session is stale
    assert md2.sessions["s1"].state == "idle"
    md3 = Model()
    restore(md3, data, mono=5, wall=60_000)               # hours later: session dropped, 5h window passed
    assert not md3.sessions
    assert md3.rates(60_000) == {"h5": 0}


def test_corrupt_or_missing_file_starts_empty(tmp_path):
    p = tmp_path / "state.json"
    md = Model()
    assert not Store(p).load(md, 1)
    p.write_text('{"v":1,"sessions":[{"nosid":1}]}')
    assert not Store(p).load(md, 1) and not md.sessions
    p.write_text("not json")
    assert not Store(p).load(md, 1)


def test_new_fields_survive_a_restart():
    md = busy_model(100)
    md.today.roll(10_003)
    md.today.turns, md.today.active_s, md.today.tokens = 4, 125.5, 9_000
    md.sessions["s1"].done_away, md.sessions["s1"].done_at = True, 101
    data = json.loads(json.dumps(dump(md, 103, 10_003)))
    md2 = Model(wall=lambda: 10_007)
    restore(md2, data, mono=7, wall=10_007)
    s = md2.sessions["s1"]
    assert s.done_away and s.done_at == 1  # wall 10_001 in the new clock (off 10_000)
    assert s.in_flight["t1"].started == 1
    assert (md2.today.turns, md2.today.active_s, md2.today.tokens) == (4, 125.5, 9_000)


def test_old_four_item_tools_still_load():
    data = json.loads(json.dumps(dump(busy_model(100), 103, 10_003)))
    for d in data["sessions"]:
        d["in_flight"] = [t[:4] for t in d["in_flight"]]
        d["recent"] = [t[:4] for t in d["recent"]]
    md = Model()
    restore(md, data, mono=7, wall=10_007)
    assert md.sessions["s1"].in_flight["t1"].started == 0.0
