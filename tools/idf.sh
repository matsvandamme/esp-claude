#!/bin/bash
# Run idf.py inside firmware/ with the ESP-IDF environment loaded.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "${IDF_PATH:-$HOME/esp/esp-idf}/export.sh" >/dev/null 2>&1
cd "$ROOT/firmware"
exec idf.py "$@"
