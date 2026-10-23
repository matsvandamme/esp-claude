#pragma once
#include "cJSON.h"
#include "proto.h"

// Build the status screen. Call once on the LVGL task after display_init().
void ui_init(void);

// proto handler for "snap" / "hb" messages (runs on the LVGL task).
void ui_on_message(const cJSON *msg, proto_src_t src);

// Short/long press of the BOOT button (LVGL task).
void ui_on_button(bool long_press);
