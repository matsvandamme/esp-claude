import random

from claude_display.model import STALE_WORKING_S, Model, model_label
from claude_display.protocol import build_snapshot, snapshot_key

CWD = "/Users/me/Projects/esp-claude"


def hook(ev, sid="s1", **kw):
    return {"hook_event_name": ev, "session_id": sid, "cwd": CWD, "transcript_path": f"/tmp/{sid}.jsonl", **kw}


def pre(uid, tool="Bash", sid="s1", **ti):
    return hook("PreToolUse", sid, tool_name=tool, tool_use_id=uid, tool_input=ti or {"command": "npm test"})


def post(uid, tool="Bash", sid="s1", fail=False):
    return hook("PostToolUseFailure" if fail else "PostToolUse", sid, tool_name=tool, tool_use_id=uid)


def m():
    return Model(rng=random.Random(1))


def test_full_turn():
    md = m()
    md.on_hook(hook("SessionStart", model="claude-opus-5-5", source="startup"), 0)
    s = md.sessions["s1"]
    assert s.state == "idle" and s.project == "esp-claude" and s.model == "Opus 5.5"
    md.on_hook(hook("UserPromptSubmit", prompt="secret prompt text"), 1)
    assert s.state == "think" and s.verb
    md.on_hook(pre("t1"), 2)
    assert s.state == "tool" and s.recent[0].name == "Bash" and s.recent[0].target == "npm test"
    md.on_hook(post("t1"), 3)
    assert s.state == "think" and s.recent[0].status == 0 and s.ntools == 1
    md.on_hook(hook("Stop"), 10)
    assert s.state == "done" and s.turn_elapsed == 9
    md.on_hook(hook("Notification", notification_type="idle_prompt", message="waiting"), 70)
    assert s.state == "idle"
    md.on_hook(hook("SessionEnd", reason="prompt_input_exit"), 80)
    assert "s1" not in md.sessions


def test_prompt_text_never_stored():
    md = m()
    md.on_hook(hook("UserPromptSubmit", prompt="my secret plan"), 0)
    snap = build_snapshot(md, 1)
    assert "my secret plan" not in str(snap) and "my secret plan" not in str(vars(md.sessions["s1"]))


def test_parallel_tools_paired_by_id():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("a", "Read", file_path=CWD + "/a.py"), 1)
    md.on_hook(pre("b", "Grep", pattern="foo"), 1.1)
    md.on_hook(post("a", "Read"), 1.2)
    s = md.sessions["s1"]
    assert s.state == "tool", "b still running"
    assert [t.name for t in s.recent] == ["Grep", "Read"]
    md.on_hook(post("b", "Grep"), 1.3)
    assert s.state == "think"


def test_post_before_pre_out_of_order_is_harmless():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(post("x"), 1)
    assert md.sessions["s1"].state == "think"


def test_failed_tool_marked():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("t"), 1)
    md.on_hook(post("t", fail=True), 2)
    assert md.sessions["s1"].recent[0].status == 2


def test_permission_request_is_instant_and_cleared_by_post():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("t", command="rm -rf build/"), 1)
    md.on_hook(hook("PermissionRequest", tool_name="Bash", tool_input={"command": "rm -rf build/"}), 1.1)
    s = md.sessions["s1"]
    assert s.state == "perm" and s.reason == "Bash: rm -rf build/"
    f = build_snapshot(md, 31.1)["f"]
    assert f["st"] == "perm" and f["ws"] == 30
    md.on_hook(post("t"), 40)
    assert s.state == "think" and s.reason == ""


def test_notification_permission_prompt_fallback():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(hook("Notification", notification_type="permission_prompt", message="Claude needs your permission"), 10)
    s = md.sessions["s1"]
    assert s.state == "perm" and s.wait_since == 4


def test_ask_user_question_and_exit_plan_mode():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("q", "AskUserQuestion", questions=[{"header": "Layout", "question": "Which?"}]), 1)
    assert md.sessions["s1"].state == "ask"
    md.on_hook(post("q", "AskUserQuestion"), 5)
    assert md.sessions["s1"].state == "think"
    md.on_hook(pre("p", "ExitPlanMode", plan="..."), 6)
    assert md.sessions["s1"].state == "ask" and "Plan" in md.sessions["s1"].reason


def test_elicitation():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(hook("Elicitation", mcp_server_name="github", message="token?"), 1)
    assert md.sessions["s1"].state == "ask"
    md.on_hook(hook("ElicitationResult", mcp_server_name="github", action="accept"), 2)
    assert md.sessions["s1"].state == "think"


def test_stop_failure_shows_error():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(hook("StopFailure", error="server_error"), 5)  # rate_limit has its own screen (D35)
    s = md.sessions["s1"]
    assert s.state == "error" and s.reason == "server error"


def test_compaction_round_trip():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(hook("PreCompact", trigger="auto"), 1)
    assert md.sessions["s1"].state == "compact"
    md.on_hook(hook("PostCompact", trigger="auto"), 5)
    assert md.sessions["s1"].state == "think"


def test_subagent_events_do_not_change_main_state():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("main", "Agent", description="Explore code"), 1)
    md.on_hook(hook("SubagentStart", agent_id="ag1", agent_type="Explore"), 1.1)
    md.on_hook({**pre("sub1", "Grep", pattern="x"), "agent_id": "ag1"}, 2)
    md.on_hook({**hook("Stop"), "agent_id": "ag1"}, 3)
    s = md.sessions["s1"]
    assert s.state == "tool" and len(s.subagents) == 1 and s.recent[0].name == "Agent"
    md.on_hook(hook("SubagentStop", agent_id="ag1"), 4)
    assert len(s.subagents) == 0


def test_subagent_permission_request_still_needs_user():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook({**hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}), "agent_id": "ag1"}, 1)
    assert md.sessions["s1"].state == "perm"


def test_transcript_interrupt_and_denial_repair_state():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("t"), 1)
    md.on_transcript_interrupt("s1", 2)
    assert md.sessions["s1"].state == "idle"
    md.on_hook(hook("UserPromptSubmit"), 3)
    md.on_hook(pre("t2"), 4)
    md.on_hook(hook("PermissionRequest", tool_name="Bash", tool_input={}), 4.1)
    md.on_transcript_interrupt("s1", 6, denied=True)
    assert md.sessions["s1"].state == "idle" and md.sessions["s1"].recent[0].status == 2


def test_stale_working_session_goes_idle_and_silent_sessions_drop():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.tick(STALE_WORKING_S + 1)
    assert md.sessions["s1"].state == "idle"
    md.tick(STALE_WORKING_S + 31 * 60)
    assert "s1" not in md.sessions


def test_focus_priority_and_button_pin():
    md = m()
    md.on_hook(hook("SessionStart", sid="a"), 0)
    md.on_hook(hook("SessionStart", sid="b"), 1)
    md.on_hook(hook("UserPromptSubmit", sid="a"), 2)
    assert md.focus(3).sid == "a"
    md.on_hook(hook("PermissionRequest", sid="b", tool_name="Bash", tool_input={}), 4)
    assert md.focus(5).sid == "b", "needs-you jumps to the front"
    md.on_hook(post("x", sid="b"), 6)  # b answered
    md.on_hook(hook("Stop", sid="b"), 7)
    assert md.focus(8).sid == "a", "working beats done"
    md.on_button(9)
    assert md.focus(10).sid == "b", "button pins the next session"
    md.on_hook(hook("PermissionRequest", sid="a", tool_name="Bash", tool_input={}), 11)
    assert md.focus(12).sid == "b", "the pin holds; the waiting session becomes a badge"
    assert md.other_alert(12, md.focus(12))["p"].endswith("·a")
    assert md.focus(9 + 600).sid == "b", "the pin has no timeout: it lasts until the next choice"


def test_same_cwd_sessions_get_suffix():
    md = m()
    md.on_hook(hook("SessionStart", sid="aaaa1111"), 0)
    md.on_hook(hook("SessionStart", sid="bbbb2222"), 1)
    labels = sorted(s.project for s in md.sessions.values())
    assert labels == ["esp-claude·aaaa", "esp-claude·bbbb"]
    md.on_hook(hook("SessionEnd", sid="bbbb2222"), 2)
    assert md.sessions["aaaa1111"].project == "esp-claude"


def test_status_line_metrics():
    md = m()
    md.on_status(
        {
            "session_id": "s1",
            "cwd": CWD,
            "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
            "cost": {"total_cost_usd": 0.4234},
            "context_window": {
                "context_window_size": 200000,
                "used_percentage": 52.4,
                "current_usage": {"input_tokens": 4000, "cache_creation_input_tokens": 50000,
                                  "cache_read_input_tokens": 50000, "output_tokens": 900},
            },
            "rate_limits": {"five_hour": {"used_percentage": 23.5}, "seven_day": {"used_percentage": 41.2}},
        },
        0,
    )
    snap = build_snapshot(md, 1)
    # D33: the API-equivalent cost is not sent to the display.
    assert snap["m"] == {"ctx": 52, "ctk": 104000, "cw": 200000, "h5": 24, "d7": 41}
    assert snap["f"]["m"] == "Opus 5.5"


def test_status_line_nulls_tolerated():
    md = m()
    md.on_status({"session_id": "s1", "context_window": {"used_percentage": None, "current_usage": None}}, 0)
    assert build_snapshot(md, 0)["m"]["ctx"] == -1


def test_snapshot_key_ignores_ticking_fields():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    assert snapshot_key(build_snapshot(md, 1)) == snapshot_key(build_snapshot(md, 5))


def test_model_labels():
    assert model_label("claude-opus-5-5") == "Opus 5.5"
    assert model_label("claude-sonnet-4-20250514") == "Sonnet 4"
    assert model_label("claude-haiku-5-5[1m]") == "Haiku 5.5"
    assert model_label("Opus") == "Opus"
    assert model_label(None) == ""


def test_unknown_events_ignored():
    md = m()
    md.on_hook(hook("SessionStart"), 0)
    v = md.version
    md.on_hook(hook("FileChanged", file_path="/x"), 1)
    assert md.version == v


def test_focus_is_sticky_between_busy_sessions():
    md = m()
    md.on_hook(hook("UserPromptSubmit", sid="a"), 0)
    md.on_hook(hook("UserPromptSubmit", sid="b"), 1)
    first = md.focus(1.5).sid
    t = 2.0
    for i in range(20):
        sid = "a" if i % 2 else "b"
        md.on_hook(pre(f"t{i}", sid=sid), t)
        assert md.focus(t).sid == first
        md.on_hook(post(f"t{i}", sid=sid), t + 0.1)
        assert md.focus(t + 0.1).sid == first
        t += 0.5


def test_focus_moves_when_current_drops():
    md = m()
    md.on_hook(hook("UserPromptSubmit", sid="a"), 0)
    md.on_hook(hook("UserPromptSubmit", sid="b"), 1)
    cur = md.focus(2).sid
    md.on_hook(hook("Stop", sid=cur), 3)
    assert md.focus(4).sid != cur


def test_error_stops_outranking_work_after_hold():
    md = m()
    md.on_hook(hook("UserPromptSubmit", sid="a"), 0)
    md.on_hook(hook("UserPromptSubmit", sid="b"), 1)
    md.on_hook(hook("StopFailure", sid="a", error="overloaded"), 2)
    assert md.focus(3).sid == "a"
    md.on_hook(pre("x", sid="b"), 50)
    assert md.focus(60).sid == "a"
    assert md.focus(2 + 91).sid == "b"


def test_payloads_without_session_id_ignored():
    md = m()
    md.on_status({}, 0)
    md.on_hook({"hook_event_name": "Stop"}, 0)
    assert md.sessions == {}


def sess(md, sid, cwd, t):
    md.on_hook({"hook_event_name": "SessionStart", "session_id": sid, "cwd": cwd}, t)


def test_focused_ghostty_window_picks_the_session():
    md = m()
    sess(md, "a", "/p/alpha", 0)
    sess(md, "b", "/p/beta", 1)
    md.on_hook(hook("UserPromptSubmit", sid="a") | {"cwd": "/p/alpha"}, 2)
    md.set_window(True, "/p/beta", 3)
    assert md.focus(3).sid == "b", "window beats a busier session"
    md.set_window(False, None, 4)
    assert md.focus(4).sid == "b", "switching to another app keeps the last focused session"
    md.set_window(True, "/Users/me", 5)
    assert md.focus(5).sid == "b", "a Ghostty tab without Claude keeps it too"
    md.set_window(True, "/p/alpha/src", 6)
    assert md.focus(6).sid == "a", "subdirectory of a session's cwd matches"


def test_boot_and_window_choice_latest_wins():
    md = m()
    sess(md, "a", "/p/alpha", 0)
    sess(md, "b", "/p/beta", 1)
    md.set_window(True, "/p/alpha", 2, "t1")
    assert md.focus(2).sid == "a"
    md.on_button(3)
    assert md.focus(4).sid == "b", "BOOT shows the next session"
    assert md.focus(4 + 600).sid == "b", "and stays there: no timer takes it back"
    md.set_window(True, "/p/alpha", 5, "t1")
    assert md.focus(5).sid == "b", "the same terminal reporting again is not a new choice"
    md.set_window(False, None, 6)
    assert md.focus(6).sid == "b", "switching to another app is not a choice either"
    md.set_window(True, "/p/alpha", 7, "t1")
    assert md.focus(7).sid == "a", "coming back to Ghostty selects its terminal"
    md.on_button(8)
    assert md.focus(8).sid == "b", "BOOT after a window choice wins"
    md.set_window(True, "/p/beta", 9, "t2")
    assert md.focus(9).sid == "b"
    md.on_button(10)
    assert md.focus(10).sid == "a", "BOOT moves on from what's on screen"
    md.set_window(True, "/p/alpha", 11, "t1")
    md.set_window(True, "/p/beta", 12, "t2")
    assert md.focus(12).sid == "b", "a window choice after BOOT wins"


def test_session_started_in_focused_window_takes_focus():
    md = m()
    sess(md, "a", "/p/alpha", 0)
    md.set_window(True, "/p/new", 1)
    assert md.focus(1).sid == "a"
    sess(md, "n", "/p/new", 2)
    assert md.focus(2).sid == "n"


def test_other_session_waiting_is_a_badge_not_a_takeover():
    md = m()
    sess(md, "a", "/p/alpha", 0)
    sess(md, "b", "/p/beta", 1)
    md.set_window(True, "/p/alpha", 2)
    md.on_hook({"hook_event_name": "PermissionRequest", "session_id": "b", "cwd": "/p/beta", "tool_name": "Bash",
                "tool_input": {"command": "git push"}}, 3)
    snap = build_snapshot(md, 4)
    assert snap["f"]["p"] == "alpha"
    assert snap["ob"] == {"p": "beta", "w": "Bash: git push", "n": 1}
    md.set_window(True, "/p/beta", 5)
    snap = build_snapshot(md, 6)
    assert snap["f"]["st"] == "perm" and "ob" not in snap


def test_plan_limit_reset_countdown():
    md = m()
    md.on_status({"session_id": "s1", "cwd": CWD, "rate_limits": {
        "five_hour": {"used_percentage": 23, "resets_at": 1000 + 7200},
        "seven_day": {"used_percentage": 41, "resets_at": 1000 + 3 * 86400}}}, 0)
    snap = build_snapshot(md, 1, wall=1000)
    assert snap["m"]["h5r"] == 7200 and snap["m"]["d7r"] == 3 * 86400
    assert snapshot_key(snap) == snapshot_key(build_snapshot(md, 1, wall=1500)), "countdown alone is not a change"


def _rl(md, sid, h5, h5r, d7=None, d7r=None, now=0):
    rl = {"five_hour": {"used_percentage": h5, "resets_at": h5r}}
    if d7 is not None:
        rl["seven_day"] = {"used_percentage": d7, "resets_at": d7r}
    md.on_status({"session_id": sid, "cwd": CWD, "rate_limits": rl}, now)


def test_rate_limits_idle_session_cannot_lower_usage():
    """D34: an idle session's status line carries its last (older) numbers."""
    md = m()
    _rl(md, "busy", 40, 10_000, 70, 500_000)
    _rl(md, "idle", 12, 10_003, 61, 499_998)  # same windows, jittered reset, stale usage
    assert md.rates(1000) == {"h5": 40, "h5r": 9000, "d7": 70, "d7r": 499_000}
    _rl(md, "busy", 42, 10_001)
    assert md.rates(1000)["h5"] == 42


def test_rate_limits_new_window_replaces_and_old_window_is_ignored():
    md = m()
    _rl(md, "a", 95, 10_000)
    _rl(md, "b", 3, 28_000)          # the 5h window rolled over
    assert md.rates(9000)["h5"] == 3
    _rl(md, "c", 95, 10_000)         # a session still reporting the previous window
    assert md.rates(9000) == {"h5": 3, "h5r": 19_000}


def test_rate_limit_window_that_passed_counts_as_unused():
    md = m()
    _rl(md, "a", 88, 10_000, 50, 90_000)
    snap = build_snapshot(md, 1, wall=10_001)
    assert snap["m"]["h5"] == 0 and "h5r" not in snap["m"]
    assert snap["m"]["d7"] == 50 and snap["m"]["d7r"] == 79_999
    assert "usd" not in snap["m"]


def test_rate_limit_failure_shows_limit_screen_until_reset():
    md = m()
    _rl(md, "s1", 100, 20_000, 70, 400_000)
    md.on_hook(hook("UserPromptSubmit"), 1)
    md.on_hook(hook("StopFailure", error="rate_limit"), 2)
    snap = build_snapshot(md, 3, wall=15_000)
    assert snap["f"]["st"] == "limit" and snap["f"]["lr"] == 5_000 and snap["f"]["w"] == ""
    md.tick(500, wall=15_100)
    assert md.sessions["s1"].state == "limit", "no 90 s decay like a plain error"
    md.tick(600, wall=20_001)
    assert md.sessions["s1"].state == "idle"


def test_rate_limit_with_unknown_window_waits_for_next_prompt():
    md = m()
    _rl(md, "s1", 40, 20_000)  # e.g. a model-specific weekly cap: no full window is known
    md.on_hook(hook("StopFailure", error="rate_limit"), 2)
    assert build_snapshot(md, 3, wall=15_000)["f"]["lr"] == -1
    md.tick(1000, wall=25_000)
    assert md.sessions["s1"].state == "limit"
    md.on_hook(hook("UserPromptSubmit"), 1001)
    assert md.sessions["s1"].state == "think"


def test_session_name_from_slash_name_replaces_folder_in_header_and_badge():
    """D40: the header shows the name set with /name; without one, the folder name."""
    md = m()
    md.on_status({"session_id": "s1", "cwd": CWD}, 0)
    assert build_snapshot(md, 1)["f"]["p"] == "esp-claude"
    md.on_status({"session_id": "s1", "cwd": CWD, "session_name": "display polish"}, 2)
    assert build_snapshot(md, 3)["f"]["p"] == "display polish"
    md.on_status({"session_id": "s2", "cwd": "/p/other", "session_name": "deploy"}, 4)
    md.set_window(True, CWD, 4)
    md.on_hook(hook("PermissionRequest", sid="s2", cwd="/p/other", tool_name="Bash",
                    tool_input={"command": "ls"}), 5)
    assert build_snapshot(md, 6)["ob"]["p"] == "deploy"
    md.on_status({"session_id": "s1", "cwd": CWD}, 7)  # name cleared
    assert build_snapshot(md, 8)["f"]["p"] == "esp-claude"


def test_session_color_is_sent_to_the_display():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    assert "c" not in build_snapshot(md, 1)["f"]
    md.on_transcript_color("s1", 0x16A34A)
    assert build_snapshot(md, 2)["f"]["c"] == 0x16A34A
    md.on_transcript_color("s1", -1)
    assert "c" not in build_snapshot(md, 3)["f"]


# ---------- D49 done while away, D50 long tools, D52 today ----------


def test_long_turn_done_while_away_is_flagged_until_you_look():
    md = m()
    md.set_window(True, "/p/other", 0)  # looking at another terminal
    md.on_hook(hook("UserPromptSubmit"), 1)
    md.on_hook(hook("Stop"), 1 + 61)
    s = md.sessions["s1"]
    assert s.done_away and build_snapshot(md, 70)["f"]["da"] == 8
    md.set_window(False, None, 80)  # another app: still away
    assert s.done_away
    md.set_window(True, CWD, 90)  # selecting its terminal clears it
    assert not s.done_away and "da" not in build_snapshot(md, 91)["f"]


def test_done_while_looking_or_short_turn_is_not_flagged():
    md = m()
    md.set_window(True, CWD, 0)
    md.on_hook(hook("UserPromptSubmit"), 1)
    md.on_hook(hook("Stop"), 100)
    assert not md.sessions["s1"].done_away, "you were looking at it"
    md.set_window(True, "/p/other", 101)
    md.on_hook(hook("UserPromptSubmit"), 102)
    md.on_hook(hook("Stop"), 102 + 59)
    assert not md.sessions["s1"].done_away, "under a minute"


def test_done_away_cleared_by_new_prompt_and_shown_for_other_sessions():
    md = m()
    sess(md, "a", "/p/alpha", 0)
    sess(md, "b", "/p/beta", 1)
    md.set_window(False, None, 2)
    md.on_hook(hook("UserPromptSubmit", sid="a") | {"cwd": "/p/alpha"}, 2)
    md.on_hook(hook("Stop", sid="a") | {"cwd": "/p/alpha"}, 200)
    md.pinned = "b"
    snap = build_snapshot(md, 201)
    assert snap["od"] == {"p": "alpha", "n": 1}
    assert [e[4] for e in snap["ss"]] == [1, 0]
    md.on_hook(hook("UserPromptSubmit", sid="a") | {"cwd": "/p/alpha"}, 202)
    assert "od" not in build_snapshot(md, 203)


def test_tool_age_skips_approval_wait_and_subagents():
    md = m()
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("t1"), 10)
    assert build_snapshot(md, 200)["f"]["ta"] == 190
    md.on_hook(pre("t2"), 200)
    md.on_hook(hook("PermissionRequest", tool_name="Bash", tool_input={"command": "rm x"}), 200)
    assert "ta" not in build_snapshot(md, 300)["f"], "waiting for you, not running"
    md.on_hook(post("t2"), 400)  # answered; t1 is still running
    assert md.sessions["s1"].state == "tool"
    assert build_snapshot(md, 430)["f"]["ta"] == 30, "counts from the answer, not from the start"
    md2 = m()
    md2.on_hook(hook("UserPromptSubmit"), 0)
    md2.on_hook(pre("a1", tool="Agent"), 1)
    assert "ta" not in build_snapshot(md2, 600)["f"]


def test_today_counts_turns_active_time_and_tokens_and_rolls_over():
    wall = [1_792_620_000.0]  # some local day
    md = Model(rng=random.Random(1), wall=lambda: wall[0])
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(hook("UserPromptSubmit", sid="s2"), 0)
    md.on_hook(hook("UserPromptSubmit", agent_id="x"), 0)  # subagent: not a turn
    for t in range(1, 21):  # 10 s of ticks, two sessions working at once
        md.tick(t * 0.5)
    md.tick(500)  # a long gap (sleep) doesn't count
    md.on_transcript_usage("msg1", 100)
    md.on_transcript_usage("msg1", 100)  # the same reply on another line
    md.on_transcript_usage("msg1", 150)  # ...with grown usage: count the max once
    md.on_transcript_usage("msg2", 50)
    td = build_snapshot(md, 501)["td"]
    assert td == {"n": 2, "a": 9, "k": 200}
    wall[0] += 86_400
    md.tick(501.5)  # the next day starts with only this tick's 1.5 s
    assert build_snapshot(md, 502)["td"] == {"n": 0, "a": 1, "k": 0}


def test_ticking_fields_do_not_change_the_snapshot_key():
    wall = [1_792_620_000.0]
    md = Model(rng=random.Random(1), wall=lambda: wall[0])
    md.on_hook(hook("UserPromptSubmit"), 0)
    md.on_hook(pre("t1"), 1)
    a = build_snapshot(md, 5)
    md.tick(5.5)
    wall[0] += 9
    b = build_snapshot(md, 14)
    assert a["ss"][0][2] != b["ss"][0][2] and a["f"]["ta"] != b["f"]["ta"] and a["tm"] != b["tm"]
    assert snapshot_key(a) == snapshot_key(b)
