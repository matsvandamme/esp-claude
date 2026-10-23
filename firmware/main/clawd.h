#pragma once
// Clawd, the Claude Code mascot, drawn procedurally on a small canvas.
// The body follows the startup banner's 18x5 pixel grid (one grid unit = 6 px);
// eyes, arms and legs vary per pose.
#include "lvgl.h"

typedef enum {
    CLAWD_IDLE = 0,
    CLAWD_THINK,
    CLAWD_WORK,
    CLAWD_ALERT,
    CLAWD_ALERT_URGENT,
    CLAWD_HAPPY,
    CLAWD_ERROR,
    CLAWD_SQUISH,
    CLAWD_SLEEP,
} clawd_mood_t;

// Creates the mascot (canvas + effect label) inside `parent` with its top-left at (x, y).
void clawd_create(lv_obj_t *parent, int32_t x, int32_t y);
void clawd_set_mood(clawd_mood_t mood);
clawd_mood_t clawd_get_mood(void);
