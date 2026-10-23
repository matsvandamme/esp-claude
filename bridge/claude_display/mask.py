"""Turn tool inputs into short, secret-free display targets (decision D3).

Only the tool name plus a short target ever leaves the Mac: a command, path, pattern or
description, truncated, with anything that looks like a credential replaced by ***.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

MAX_TARGET = 40
PATH_FIT = 20  # the display's tool line has room for ~24 characters including the tool name
REDACTED = "***"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization / API-key headers: curl -H 'Authorization: Bearer x', -H "X-Api-Key: x"
    (re.compile(r"(?i)\b(authorization|proxy-authorization|x-api-key|api-key|cookie)\s*:\s*[^'\"\n]+"),
     r"\1: " + REDACTED),
    # Bearer/Basic tokens on their own
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    # --password=x / --token x / --api-key x / -p x (mysql style)
    (re.compile(r"(?i)(--?(?:password|passwd|pass|pwd|token|secret|api[-_]?key|access[-_]?key|auth)[= ])\S+"),
     r"\1" + REDACTED),
    (re.compile(r"(?<=\s)-p(?=\S{4,})\S+"), "-p" + REDACTED),
    # KEY=value env prefixes for secret-looking names
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIALS?)"
                r"[A-Z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)"), r"\1=" + REDACTED),
    # user:password@host in URLs
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^/\s:@]+):[^@\s/]+@"), r"\1:" + REDACTED + "@"),
    # Well-known token formats
    (re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}"), REDACTED),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), REDACTED),  # JWT
    # Long hex / base64 blobs
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), REDACTED),
    (re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9/+])"), REDACTED),
]


def mask_secrets(text: str) -> str:
    for pat, repl in _PATTERNS:
        text = pat.sub(repl, text)
    return text


def truncate(text: str, limit: int = MAX_TARGET) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def short_path(path: str, cwd: str | None) -> str:
    if cwd and path.startswith(cwd.rstrip("/") + "/"):
        path = path[len(cwd.rstrip("/")) + 1 :]
    home = os.path.expanduser("~")
    if path.startswith(home + "/"):
        path = "~" + path[len(home) :]
    return fit_path(path)


def fit_path(path: str, limit: int = PATH_FIT) -> str:
    """Keep the end of a long path, where the file name is: …/parent/name, else name."""
    if len(path) <= limit:
        return path
    parts = [p for p in path.rstrip("/").split("/") if p]
    if not parts:
        return path
    tail = "…/" + "/".join(parts[-2:])
    return tail if len(parts) > 2 and len(tail) <= limit else parts[-1]


_ABS_PATH = re.compile(r"(?<![\w.-])(?:~|/)[^\s'\"]*/[^\s'\"]+")


def shorten_paths(cmd: str) -> str:
    """`cd /private/tmp/claude-501/x/e2e/run3` -> `cd …/e2e/run3`."""
    return _ABS_PATH.sub(lambda m: fit_path(m.group(0)), cmd)


def tool_display_name(tool_name: str) -> str:
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        return parts[-1] if len(parts) >= 3 else tool_name
    return tool_name


def tool_target(tool_name: str, tool_input: dict | None, cwd: str | None = None) -> str:
    """Short, masked description of what a tool call operates on."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    s = ""
    if tool_name == "Bash":
        cmd = str(ti.get("command", "")).strip().splitlines()
        s = shorten_paths(mask_secrets(cmd[0])) if cmd else ""
    elif tool_name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "NotebookRead"):
        p = ti.get("file_path") or ti.get("notebook_path") or ""
        return truncate(mask_secrets(short_path(str(p), cwd)))
    elif tool_name in ("Glob", "Grep"):
        s = str(ti.get("pattern", ""))
    elif tool_name == "WebFetch":
        url = str(ti.get("url", ""))
        try:
            u = urlsplit(url)
            s = (u.netloc + u.path) if u.netloc else url
        except ValueError:
            s = url
    elif tool_name == "WebSearch":
        s = str(ti.get("query", ""))
    elif tool_name in ("Task", "Agent"):
        s = str(ti.get("description", "") or ti.get("subagent_type", ""))
    elif tool_name in ("TaskCreate", "TaskUpdate"):
        s = str(ti.get("subject", "") or ti.get("description", ""))
    elif tool_name == "AskUserQuestion":
        qs = ti.get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            s = str(qs[0].get("header") or qs[0].get("question") or "")
    elif tool_name == "Skill":
        s = str(ti.get("skill", ""))
    else:
        for key in ("description", "path", "file_path", "url", "query", "name"):
            if isinstance(ti.get(key), str):
                s = ti[key]
                break
    return truncate(mask_secrets(s))
