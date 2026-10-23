#pragma once
#include <stdint.h>

// Frame timing: render start (LVGL task) -> last area's DMA transfer done (ISR).
void stats_frame_started(void);
void stats_frame_flushed(void);

// Render a one-line JSON stats report into buf (frames/s and frame times over the
// window since the previous call, plus heap figures). Resets the window.
int stats_report_json(char *buf, int len, const char *link);

// Start a fresh measurement window (e.g. after a screenshot stalled a frame).
void stats_reset(void);
