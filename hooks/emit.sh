#!/bin/sh
# Claude Code hook -> claude-display bridge. Must never print, block or fail (decision D9):
# stdout on some events is fed to the model, and a non-zero exit shows a hook error.
curl -s -m 0.5 -o /dev/null --data-binary @- http://127.0.0.1:47321/hook >/dev/null 2>&1
exit 0
