"""Tail session transcripts for the two events no hook reports (decision D11):
an Esc interrupt and a denied permission. Only new lines are read, never history,
and only structural markers are matched, so quoted text in a conversation can't trigger them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

INTERRUPT_PREFIX = "[Request interrupted by user"
DENIED_PREFIX = "The user doesn't want to proceed with this tool use"
MAX_READ = 4 * 1024 * 1024
COLOR_MARK = b'"type":"agent-color"'
COLOR_SCAN_MAX = 64 * 1024 * 1024
# The colours /color offers, as Claude Code's theme draws them (2.1.296 binary).
SESSION_COLORS = {
    "red": 0xDC2626, "blue": 0x6A9BCC, "green": 0x16A34A, "yellow": 0xCA8A04,
    "purple": 0x827DBD, "orange": 0xD97757, "pink": 0xC46686, "cyan": 0x0891B2,
}


@dataclass
class Finding:
    kind: str  # "activity" | "interrupt" | "denied"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                return str(b.get("text", ""))
    return ""


def usage_of(d: dict) -> str | None:
    """'usage:<message id>:<new tokens>' for an assistant reply (D52): input + output + cache
    creation. Cache reads are left out: every call re-reads the whole context."""
    msg = d.get("message")
    if not isinstance(msg, dict) or not msg.get("id") or not isinstance(msg.get("usage"), dict):
        return None
    u = msg["usage"]
    try:
        n = sum(int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens"))
    except (TypeError, ValueError):
        return None
    return f"usage:{msg['id']}:{n}"


def classify_line(line: str) -> str | None:
    """Return 'interrupt', 'denied', 'activity', 'color:…', 'usage:…' or None for a transcript JSONL line."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("type") == "agent-color":  # written by /color; "default" clears it
        c = str(d.get("agentColor", ""))
        return f"color:{c if c in SESSION_COLORS else ''}"
    if d.get("isSidechain"):
        return "activity"
    if d.get("type") != "user":
        return (usage_of(d) or "activity") if d.get("type") == "assistant" else None
    msg = d.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return "interrupt" if content.startswith(INTERRUPT_PREFIX) else "activity"
    if isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and str(b.get("text", "")).startswith(INTERRUPT_PREFIX):
                return "interrupt"
            if b.get("type") == "tool_result" and _text_of(b.get("content")).startswith(DENIED_PREFIX):
                return "denied"
    return "activity"


class TranscriptTail:
    """Tracks read offsets per transcript file; the first poll of a file starts at its end."""

    def __init__(self) -> None:
        self._offsets: dict[str, int] = {}
        self._partial: dict[str, bytes] = {}

    def forget(self, path: str) -> None:
        self._offsets.pop(path, None)
        self._partial.pop(path, None)

    def poll(self, path: str) -> list[str]:
        """Return the classification of each complete new line (dropping None)."""
        try:
            size = os.path.getsize(path)
        except OSError:
            return []
        if path not in self._offsets:
            self._offsets[path] = size
            color = last_color(path, size)
            return [color] if color else []
        off = self._offsets[path]
        if size < off:  # truncated / rewritten
            off = 0
            self._partial.pop(path, None)
        if size == off:
            return []
        with open(path, "rb") as f:
            f.seek(off)
            data = f.read(min(size - off, MAX_READ))
        self._offsets[path] = off + len(data)
        data = self._partial.pop(path, b"") + data
        lines = data.split(b"\n")
        if lines and lines[-1]:
            self._partial[path] = lines[-1]
        out = []
        for raw in lines[:-1]:
            kind = classify_line(raw.decode("utf-8", "replace"))
            if kind:
                out.append(kind)
        return out


def last_color(path: str, size: int) -> str | None:
    """The newest /color entry already in a transcript (read once, when tailing starts)."""
    chunk = 1024 * 1024
    end = size
    try:
        with open(path, "rb") as f:
            while end > 0 and size - end < COLOR_SCAN_MAX:
                start = max(0, end - chunk)
                f.seek(start)
                data = f.read(end - start + 4096)  # overlap so a line split at the boundary is seen
                i = data.rfind(COLOR_MARK)
                if i >= 0:
                    a = data.rfind(b"\n", 0, i) + 1
                    b = data.find(b"\n", i)
                    found = classify_line(data[a:b if b >= 0 else len(data)].decode("utf-8", "replace"))
                    if found:
                        return found
                    end = start + i  # line cut at the chunk start: look again from further back
                    continue
                end = start
    except OSError:
        pass
    return None
