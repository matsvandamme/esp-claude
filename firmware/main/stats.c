#include "stats.h"

#include <stdio.h>

#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "display.h"
#include "led.h"
#include "link_wifi.h"
#include "ota.h"

static volatile int64_t s_start_us;
static volatile uint32_t s_frames;
static volatile int64_t s_sum_us;
static volatile int64_t s_max_us;
static int64_t s_window_start_us;

void stats_frame_started(void)
{
    if (s_start_us == 0) {
        s_start_us = esp_timer_get_time();
    }
}

void stats_frame_flushed(void)
{
    int64_t start = s_start_us;
    if (start == 0) {
        return;
    }
    int64_t d = esp_timer_get_time() - start;
    s_start_us = 0;
    s_frames++;
    s_sum_us += d;
    if (d > s_max_us) {
        s_max_us = d;
    }
}

void stats_reset(void)
{
    s_frames = 0;
    s_sum_us = 0;
    s_max_us = 0;
    s_start_us = 0;
    s_window_start_us = esp_timer_get_time();
}

// Call on the LVGL task: the stack figure is the caller's.
int stats_report_json(char *buf, int len, const char *link)
{
    lv_mem_monitor_t mon;
    lv_mem_monitor(&mon);
    int64_t now = esp_timer_get_time();
    int64_t window = now - s_window_start_us;
    uint32_t frames = s_frames;
    int64_t sum = s_sum_us, max = s_max_us;
    s_frames = 0;
    s_sum_us = 0;
    s_max_us = 0;
    s_window_start_us = now;

    float fps = window > 0 ? frames * 1e6f / (float)window : 0;
    float avg_ms = frames ? (float)sum / frames / 1000.0f : 0;
    return snprintf(buf, len,
                    "{\"t\":\"stats\",\"fps\":%.1f,\"favg\":%.2f,\"fmax\":%.2f,\"frames\":%u,"
                    "\"heap\":%u,\"heap_min\":%u,\"dma_big\":%u,\"lv_free\":%u,\"lv_min_free\":%u,"
                    "\"stack_free\":%u,\"bl\":%u,\"led\":\"%s\",\"wifi\":\"%s\",\"rssi\":%d,\"udp_drop\":%lu,"
                    "\"up\":%lld,\"link\":\"%s\",\"ver\":\"%s\",\"rst\":%d}\n",
                    fps, avg_ms, (float)max / 1000.0f, (unsigned)frames,
                    (unsigned)heap_caps_get_free_size(MALLOC_CAP_8BIT),
                    (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT),
                    (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA), (unsigned)mon.free_size,
                    (unsigned)(mon.total_size - mon.max_used), (unsigned)uxTaskGetStackHighWaterMark(NULL),
                    (unsigned)display_get_backlight(), led_mode_name(), link_wifi_state(), link_wifi_rssi(),
                    (unsigned long)link_wifi_drops(), now / 1000000, link, ota_running_label(), (int)esp_reset_reason());
}
