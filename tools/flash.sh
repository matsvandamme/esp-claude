#!/bin/bash
# Build+flash the firmware to the display board only (resolved by USB serial number,
# see `python -m claude_display.device --list/--save`).
# The bridge LaunchAgent holds the port exclusively, so it is paused while flashing.
# Usage: tools/flash.sh [extra idf.py args, e.g. erase-flash]
#        tools/flash.sh --wifi   build, then update over WiFi through the running bridge (D37)
# Note: plain flashing keeps the `creds` partition (WiFi); erase-flash wipes it, so re-run
#       tools/provision_wifi.py build && flash afterwards.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$1" = "--wifi" ]; then
  "$ROOT/tools/idf.sh" build >/dev/null
  BIN="$ROOT/firmware/build/esp_claude.bin"
  echo "updating over WiFi: $(stat -f %z "$BIN") bytes" >&2
  out="$(curl -s -m 200 --data-binary "{\"path\":\"$BIN\"}" http://127.0.0.1:47321/ota)"
  echo "$out" | python3 -m json.tool
  echo "$out" | grep -q '"ok": true'
  exit $?
fi
LABEL="com.github.matsvandamme.esp-claude"  # keep in sync with tools/install.py
AGENT="gui/$(id -u)/$LABEL"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
paused=0
if launchctl print "$AGENT" >/dev/null 2>&1; then
  launchctl bootout "$AGENT" && paused=1
fi
resume() { [ "$paused" = 1 ] && launchctl bootstrap "gui/$(id -u)" "$PLIST"; }
trap resume EXIT
PORT="$(cd "$ROOT/bridge" && uv run -q python -m claude_display.device)"
if [ -z "$PORT" ]; then
  echo "display not found. Attached Espressif boards:" >&2
  (cd "$ROOT/bridge" && uv run -q python -m claude_display.device --list) >&2
  echo "pick yours with: (cd bridge && uv run python -m claude_display.device --save SERIAL)" >&2
  exit 1
fi
echo "flashing $PORT" >&2
"$ROOT/tools/idf.sh" -p "$PORT" "$@" flash
