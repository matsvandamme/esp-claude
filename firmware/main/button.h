#pragma once
#include <stdbool.h>

// Polls the BOOT button from an LVGL timer; `cb` runs on the LVGL task.
void button_init(void (*cb)(bool long_press));
