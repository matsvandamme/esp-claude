#pragma once
// Colours matching Claude Code's dark theme (as of v2.1.296), and the generated fonts.
#include "lvgl.h"

#define C_BG          0x141413
#define C_PANEL       0x1F1E1C
#define C_TEXT        0xFFFFFF
#define C_INACTIVE    0x999999
#define C_SUBTLE      0x505050
#define C_CLAUDE      0xD77757
#define C_SHIMMER     0xEB9F7F
#define C_PERMISSION  0xB1B9F9
#define C_SUCCESS     0x4EBA65
#define C_ERROR       0xFF6B80
#define C_WARNING     0xFFC107
#define C_PLAN        0x48968C
#define C_RL_FILL     0xB1B9F9
#define C_RL_EMPTY    0x505370

LV_FONT_DECLARE(font_mono_11)
LV_FONT_DECLARE(font_mono_13)
LV_FONT_DECLARE(font_mono_16b)
LV_FONT_DECLARE(font_mono_20b)
LV_FONT_DECLARE(font_clock_44b)

// UTF-8 glyphs used by the UI.
#define G_DOT      "\xC2\xB7"      // ·
#define G_ELLIPSIS "\xE2\x80\xA6"  // …
#define G_BULLET   "\xE2\x97\x8F"  // ●
#define G_CHECK    "\xE2\x9C\x93"  // ✓
#define G_CROSS    "\xE2\x9C\x97"  // ✗
#define G_WARN     "\xE2\x9A\xA0"  // ⚠
#define G_ARROW_UP "\xE2\x86\x91"  // ↑
