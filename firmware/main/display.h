#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "lvgl.h"

// Bring up SPI, the ST7789 panel and LVGL. Must be called before any other display_* call.
void display_init(void);

// Run the LVGL loop forever on the calling task. `on_tick` runs on the same task
// before each lv_timer_handler() call, so it may touch LVGL objects.
void display_run(void (*on_tick)(void));

// Backlight level 0..100 (PWM via LEDC).
void display_set_backlight(uint8_t percent);
uint8_t display_get_backlight(void);

lv_display_t *display_get(void);
