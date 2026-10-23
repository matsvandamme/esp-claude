#!/bin/bash
# Sync the committed tree (minus .gitattributes export-ignore paths) to the public GitHub
# repo as one new commit. The private history never leaves this machine.
# Usage: tools/publish_public.sh [--dry-run]
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="matsvandamme/esp-claude"
AUTHOR="Matthieu Van Damme <27813027+matsvandamme@users.noreply.github.com>"
WORK="${TMPDIR:-/tmp}/esp-claude-publish"
# Strings that must never be published (board serials, SSID, hostnames...), one extended
# regex per line in the gitignored secrets/publish_deny; your home path is always checked.
# Text files only: re-check rendered PNG/3MF/STL metadata by hand after re-rendering.
DENY_FILE="$ROOT/secrets/publish_deny"

cd "$ROOT"
[ -z "$(git status --porcelain --untracked-files=no)" ] || { echo "commit or stash changes first" >&2; exit 1; }
SHA="$(git rev-parse --short HEAD)"

rm -rf "$WORK" && mkdir -p "$WORK/staging"
git archive HEAD | tar -x -C "$WORK/staging"
if gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo clone "$REPO" "$WORK/public" -- -q
else
  git init -q -b main "$WORK/public"
fi
rsync -a --delete --exclude .git "$WORK/staging/" "$WORK/public/"

cd "$WORK/public"
if grep -rnIE -e "$HOME/" ${DENY_FILE:+$( [ -f "$DENY_FILE" ] && echo -f "$DENY_FILE")} . --exclude-dir=.git; then
  echo "refusing to publish: private strings above" >&2; exit 1
fi
git add -A
if git diff --cached --quiet; then echo "public repo already matches $SHA"; exit 0; fi
git diff --cached --stat | tail -1
if [ "$1" = "--dry-run" ]; then echo "dry run: not committed ($WORK/public)"; exit 0; fi
git -c user.name="${AUTHOR% <*}" -c user.email="$(echo "$AUTHOR" | sed 's/.*<\(.*\)>/\1/')" \
  commit -q -m "Sync from private $SHA"
if gh repo view "$REPO" >/dev/null 2>&1; then
  git push -q origin HEAD:main
else
  gh repo create "$REPO" --public --source . --push \
    --description "Claude Code status display for the Waveshare ESP32-C6-LCD-1.47"
fi
echo "published $SHA to https://github.com/$REPO"
