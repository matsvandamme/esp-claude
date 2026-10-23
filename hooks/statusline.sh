#!/bin/sh
# Claude Code status line: hand the JSON to the bridge (metrics for the display) and print
# the line it renders. Falls back to a dim marker when the bridge is not running.
out=$(curl -s -m 0.5 --data-binary @- http://127.0.0.1:47321/status 2>/dev/null)
if [ -n "$out" ]; then
  printf '%s' "$out"
else
  printf '\033[2m✻ display offline\033[0m'
fi
exit 0
