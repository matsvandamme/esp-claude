#pragma once
#include <stdbool.h>
#include <stdint.h>

// Screenshot over USB: on request, the whole screen is invalidated and redrawn, and each
// flushed area is streamed as base64 RGB565 rows: {"t":"shot","x":..,"y":..,"w":..,"d":".."}
// followed by {"t":"shot_end","w":320,"h":172}. Must be called on the LVGL task.
void shot_request(void);
bool shot_active(void);
void shot_emit_area(int32_t x, int32_t y, int32_t w, int32_t h, const uint16_t *px);
void shot_end(void);
