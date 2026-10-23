#!/bin/bash
# Generate LVGL fonts: JetBrains Mono for text + DejaVu Sans Mono for the spinner dingbats
# JetBrains lacks. DejaVu is downloaded on first use; JetBrains Mono Nerd Font is expected
# at $JB-{Regular,Bold}.ttf (https://github.com/ryanoasis/nerd-fonts, JetBrainsMono.zip).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JB="${JB:-$HOME/Library/Fonts/JetBrainsMonoNerdFont}"
CACHE="$ROOT/firmware/build/fonts"
DEJAVU="$CACHE/dejavu-fonts-ttf-2.37/ttf/DejaVuSansMono"
OUT="$ROOT/firmware/main/fonts"
ASCII="0x20-0x7E"
# · × — … → ↑ ↓ █ ░ ● ○ ⚠ ✓ ✗ ✶
JB_EXTRA="0xB7,0xD7,0x2014,0x2026,0x2192,0x2191,0x2193,0x2588,0x2591,0x25CF,0x25CB,0x26A0,0x2713,0x2717,0x2736"
# ✢ ✳ ✻ ✽
SYM_EXTRA="0x2722,0x2733,0x273B,0x273D"
if [ ! -f "$DEJAVU.ttf" ]; then
  mkdir -p "$CACHE"
  curl -sSL https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2 \
    | tar xj -C "$CACHE"
fi
# lv_font_conv writes its full command line into the header; keep only file names there.
strip_paths() { sed -i '' -E "4s#(--font |-o )[^ ]*/#\1#g" "$1"; }
gen() { # name size weight
  local name=$1 size=$2 w=$3 sym="$DEJAVU.ttf"
  [ "$w" = Bold ] && sym="$DEJAVU-Bold.ttf"
  npx --yes lv_font_conv@1.5.3 --no-compress --bpp 4 --size "$size" --format lvgl --lv-include lvgl.h \
    --lv-font-name "$name" \
    --font "$JB-$w.ttf" -r "$ASCII" -r "$JB_EXTRA" \
    --font "$sym" -r "$SYM_EXTRA" \
    -o "$OUT/$name.c"
  strip_paths "$OUT/$name.c"
}
# The idle clock (D46): digits and ':' only, so the big size stays small in flash.
gen_clock() {
  npx --yes lv_font_conv@1.5.3 --no-compress --bpp 4 --size 44 --format lvgl --lv-include lvgl.h \
    --lv-font-name font_clock_44b --font "$JB-Bold.ttf" -r 0x30-0x3A -o "$OUT/font_clock_44b.c"
  strip_paths "$OUT/font_clock_44b.c"
}
if [ "$1" = "clock" ]; then gen_clock; exit 0; fi
gen font_mono_11 11 Regular
gen font_mono_13 13 Regular
gen font_mono_16b 16 Bold
gen font_mono_20b 20 Bold
gen_clock
