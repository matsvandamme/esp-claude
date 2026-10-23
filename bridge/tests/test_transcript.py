import json

from claude_display.transcript import TranscriptTail, classify_line


def line(d):
    return json.dumps(d) + "\n"


INTERRUPT = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": "[Request interrupted by user]"}]}}
INTERRUPT_TOOL = {"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}}
DENIED = {
    "type": "user",
    "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": True,
                             "content": "The user doesn't want to proceed with this tool use. The tool use was rejected"}]},
}
ASSISTANT = {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 3}}}
QUOTED = {"type": "user", "message": {"content": [{"type": "text", "text": "why does it say [Request interrupted by user]?"}]}}
QUOTED_TOOL_OUTPUT = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "grep: The user doesn't want"}]}}


def test_classify():
    assert classify_line(json.dumps(INTERRUPT)) == "interrupt"
    assert classify_line(json.dumps(INTERRUPT_TOOL)) == "interrupt"
    assert classify_line(json.dumps(DENIED)) == "denied"
    assert classify_line(json.dumps(ASSISTANT)) == "usage:m1:3"
    assert classify_line(json.dumps({"type": "assistant", "message": {"content": []}})) == "activity"
    assert classify_line(json.dumps(QUOTED)) == "activity"
    assert classify_line(json.dumps(QUOTED_TOOL_OUTPUT)) == "activity"
    assert classify_line(json.dumps({**INTERRUPT, "isSidechain": True})) == "activity"
    assert classify_line("not json") is None


def test_tail_starts_at_end_and_handles_partial_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(line(INTERRUPT))  # history: must be ignored
    t = TranscriptTail()
    assert t.poll(str(p)) == []
    with p.open("a") as f:
        f.write(line(ASSISTANT))
        f.write(json.dumps(DENIED)[:20])  # half-written line
    assert t.poll(str(p)) == ["usage:m1:3"]
    with p.open("a") as f:
        f.write(json.dumps(DENIED)[20:] + "\n")
    assert t.poll(str(p)) == ["denied"]


def test_tail_missing_and_truncated(tmp_path):
    t = TranscriptTail()
    assert t.poll(str(tmp_path / "nope.jsonl")) == []
    p = tmp_path / "t.jsonl"
    p.write_text(line(ASSISTANT) * 3)
    t.poll(str(p))
    p.write_text(line(INTERRUPT))
    assert t.poll(str(p)) == ["interrupt"]


def test_session_color_from_slash_color(tmp_path):
    """D41: /color writes agent-color entries; the newest one already in the file counts."""
    from claude_display.transcript import TranscriptTail, classify_line

    assert classify_line('{"type":"agent-color","agentColor":"green","sessionId":"s"}') == "color:green"
    assert classify_line('{"type":"agent-color","agentColor":"default","sessionId":"s"}') == "color:"
    quoted = json.dumps({"type": "user", "message": {"content": '{"type":"agent-color","agentColor":"red"}'}})
    assert classify_line(quoted) == "activity", "quoted text is not a colour change"
    p = tmp_path / "t.jsonl"
    filler = json.dumps({"type": "assistant", "message": {"content": "x" * 900}}) + "\n"
    p.write_text('{"type":"agent-color","agentColor":"red","sessionId":"s"}\n' + filler * 3000 +
                 '{"type":"agent-color","agentColor":"blue","sessionId":"s"}\n' + filler * 1500 + quoted + "\n")
    tail = TranscriptTail()
    assert tail.poll(str(p)) == ["color:blue"], "newest entry, found across chunk boundaries"
    with p.open("a") as f:
        f.write('{"type":"agent-color","agentColor":"pink","sessionId":"s"}\n')
    assert tail.poll(str(p)) == ["color:pink"]


def test_usage_counts_new_tokens_only():
    d = {"type": "assistant", "message": {"id": "msg_01", "usage": {
        "input_tokens": 5, "output_tokens": 400, "cache_creation_input_tokens": 1200, "cache_read_input_tokens": 90000}}}
    assert classify_line(json.dumps(d)) == "usage:msg_01:1605"
    assert classify_line(json.dumps({**d, "isSidechain": True})) == "activity"
