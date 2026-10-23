"""The snapshot schema the firmware parses (firmware/main/snap.c) and an in-process demo replay."""
import json
import random
from pathlib import Path

import pytest

from claude_display import demo
from claude_display.model import Model
from claude_display.protocol import build_snapshot, encode
from claude_display.server import Bridge

FIXTURES = sorted((Path(__file__).parents[2] / "assets" / "snapshots").glob("*.json"))
STATES = {"start", "think", "tool", "perm", "ask", "done", "idle", "error", "compact", "limit"}  # firmware snap.c
FIRMWARE_LIMITS = {"p": 27, "m": 19, "v": 23, "w": 39}  # char buffers in snap_t minus NUL


def check_snapshot(s: dict) -> None:
    assert s["t"] == "snap"
    assert len(encode(s)) <= 1400, "must fit one UDP datagram / Ethernet frame (and the 2 KB firmware line buffer)"
    assert all(st in STATES for st in s["s"])
    assert len(s["s"]) <= 8
    for e in s.get("ss", []):  # D51 overview rows
        assert len(e) == 5 and len(e[0]) <= FIRMWARE_LIMITS["p"] and e[1] in STATES
        assert all(isinstance(v, int) for v in e[2:])
    assert len(s.get("ss", [])) <= 8
    for v in (s.get("td") or {}).values():
        assert isinstance(v, int)
    if "od" in s:
        assert len(s["od"]["p"]) <= FIRMWARE_LIMITS["p"] and isinstance(s["od"]["n"], int)
    f = s["f"]
    if f is None:
        assert s["fi"] == -1
        return
    assert f["st"] in STATES
    assert 0 <= s["fi"] < len(s["s"])
    for k in ("el", "ws", "nt", "sub", "ta", "da"):
        assert isinstance(f.get(k, 0), int)
    for tool in f["tl"]:
        assert len(tool) == 3 and tool[2] in (0, 1, 2)
    for key in ("ctx", "h5", "d7"):
        if key in s.get("m", {}):
            assert isinstance(s["m"][key], int)


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixtures_match_schema(path):
    check_snapshot({**json.loads(path.read_text()), "fi": json.loads(path.read_text()).get("fi", 0)})


def test_encode_roundtrip_is_one_line_utf8():
    md = Model(rng=random.Random(0))
    md.on_hook({"hook_event_name": "PreToolUse", "session_id": "s", "cwd": "/p/ünïcode", "tool_name": "Bash",
                "tool_use_id": "t", "tool_input": {"command": "echo ✻ done"}}, 0)
    raw = encode(build_snapshot(md, 1))
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    back = json.loads(raw)
    assert back["f"]["p"] == "ünïcode" and back["f"]["tl"][0][1] == "echo ✻ done"


def test_demo_replay_produces_valid_snapshots(monkeypatch):
    """Run the demo's fake sessions in-process through the bridge and validate every snapshot."""
    clock = [0.0]
    bridge = Bridge(model=Model(rng=random.Random(5)), clock=lambda: clock[0])
    sent = []

    class Link:
        name, connected = "fake", True

        def send(self, data):
            sent.append(json.loads(data))
            return True

    bridge.add_link(Link())

    def fake_post(path, payload):
        (bridge.handle_hook if path == "/hook" else bridge.handle_status)(payload)
        bridge.maybe_send()
        return "{}"

    def sleep(s):
        clock[0] += s
        bridge.model.tick(clock[0])
        bridge.maybe_send()
        return False

    monkeypatch.setattr(demo, "post", fake_post)
    rng = random.Random(9)
    sessions = [demo.FakeSession(i, random.Random(rng.random())) for i in range(3)]
    for s in sessions:
        s.hook("SessionStart", source="startup", model="claude-opus-5-5")
    for _ in range(15):
        for s in sessions:
            s.turn(sleep)
    snaps = [m for m in sent if m["t"] == "snap"]
    assert len(snaps) > 100
    for s in snaps:
        check_snapshot(s)
        f = s["f"]
        if f:
            for k, limit in FIRMWARE_LIMITS.items():
                assert len(f.get(k, "")) <= limit or k == "p", (k, f.get(k))
    focused = {s["f"]["st"] for s in snaps if s["f"]}
    assert {"think", "tool", "perm"} <= focused
    assert "done" in {st for s in snaps for st in s["s"]}, "done sessions show in the dot strip"


def test_eight_long_sessions_fit_one_datagram():
    md = Model(rng=random.Random(0))
    for i in range(8):
        sid = f"s{i}-" + "x" * 30
        md.on_hook({"hook_event_name": "UserPromptSubmit", "session_id": sid, "cwd": f"/p/{'ü' * 40}{i}"}, i)
        md.on_status({"session_id": sid, "session_name": "n" * 60}, i)
        md.on_hook({"hook_event_name": "PreToolUse", "session_id": sid, "tool_name": "Bash", "tool_use_id": f"t{i}",
                    "tool_input": {"command": "ü" * 200}}, i)
    md.on_hook({"hook_event_name": "PermissionRequest", "session_id": "s1-" + "x" * 30, "tool_name": "Bash",
                "tool_input": {"command": "ü" * 200}}, 9)
    check_snapshot(build_snapshot(md, 10))
