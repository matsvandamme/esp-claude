import pytest

from claude_display.mask import MAX_TARGET, REDACTED, mask_secrets, tool_display_name, tool_target

SECRET_CASES = [
    ("curl -H 'Authorization: Bearer abcdef1234567890xyz' https://api.x.io", "abcdef1234567890xyz"),
    ('curl -H "X-Api-Key: 9f8e7d6c5b4a" https://x', "9f8e7d6c5b4a"),
    ("mysql -u root -phunter2secret db", "hunter2secret"),
    ("deploy --password=correcthorse", "correcthorse"),
    ("deploy --token ghx123456789", "ghx123456789"),
    ("tool --api-key s3cr3tvalue", "s3cr3tvalue"),
    ("OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx python run.py", "sk-abcdefghijklmnopqrstuvwx"),
    ("GITHUB_TOKEN='ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa' gh pr list", "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    ("echo sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV", "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUV"),
    ("git clone https://ghp_bbbbbbbbbbbbbbbbbbbbbbbbbbbb@github.com/x/y", "ghp_bbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
    ("psql postgres://admin:pa55word@db.local/app", "pa55word"),
    ("slack xoxb-1234567890-abcdefghij", "xoxb-1234567890-abcdefghij"),
    ("aws s3 ls AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk", "eyJhbGciOiJIUzI1NiJ9"),
    ("echo 4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a", "4f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a"),
    ("export DB_PASSWORD=\"x y z\"", "x y z"),
    ("cat key | base64 QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9w", "QUJDREVGR0hJSktMTU5PUFFSU1RV"),
    ("curl -u user --cookie: sessionid=abc123def", "sessionid=abc123def"),
]


@pytest.mark.parametrize("text,secret", SECRET_CASES)
def test_secrets_are_masked(text, secret):
    out = mask_secrets(text)
    assert secret not in out
    assert REDACTED in out


@pytest.mark.parametrize(
    "text",
    ["npm test", "git status", "idf.py build", "ls -la ~/Projects", "pytest -q tests/test_mask.py", "rm -rf build/"],
)
def test_harmless_commands_untouched(text):
    assert mask_secrets(text) == text


def test_bash_target_truncated_and_masked():
    t = tool_target("Bash", {"command": "curl -H 'Authorization: Bearer abcdefghijklmnop' https://example.com/" + "x" * 80})
    assert len(t) <= MAX_TARGET
    assert "abcdefghijklmnop" not in t


def test_bash_multiline_uses_first_line():
    assert tool_target("Bash", {"command": "cd x\nmake"}) == "cd x"


def test_file_paths_relative_to_cwd():
    assert tool_target("Edit", {"file_path": "/Users/me/p/firmware/main/ui.c"}, "/Users/me/p") == "firmware/main/ui.c"


def test_long_paths_shortened():
    t = tool_target("Read", {"file_path": "/a/" + "very_long_directory_name/" * 4 + "file.py"}, "/b")
    assert t.endswith("file.py") and len(t) <= MAX_TARGET


def test_webfetch_drops_query():
    assert tool_target("WebFetch", {"url": "https://x.io/a/b?token=secret123"}) == "x.io/a/b"


def test_mcp_tool_name_shortened():
    assert tool_display_name("mcp__claude-in-chrome__navigate") == "navigate"
    assert tool_display_name("Bash") == "Bash"


def test_unknown_tool_and_bad_input():
    assert tool_target("Whatever", None) == ""
    assert tool_target("Whatever", {"description": "do things"}) == "do things"


def test_paths_keep_the_file_name():
    p = "/Users/me/p"
    assert tool_target("Read", {"file_path": p + "/spec-claude-display.md"}, p) == "spec-claude-display.md"
    assert tool_target("Edit", {"file_path": p + "/bridge/claude_display/model.py"}, p) == "model.py"
    assert tool_target("Edit", {"file_path": p + "/firmware/main/fonts/f.c"}, p) == "…/fonts/f.c"
    assert tool_target("Read", {"file_path": p + "/a/b/c/ui.c"}, p) == "a/b/c/ui.c"


def test_bash_absolute_paths_shortened_but_secrets_still_masked():
    assert tool_target("Bash", {"command": "cd /private/tmp/claude-501/x/e2e/run3"}) == "cd …/e2e/run3"
    assert tool_target("Bash", {"command": "ls -la src/main"}) == "ls -la src/main"
    t = tool_target("Bash", {"command": "curl -H 'Authorization: Bearer abcdefghijklmnop' /very/long/path/to/thing"})
    assert "abcdefghijklmnop" not in t
