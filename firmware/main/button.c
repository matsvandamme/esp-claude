#include "button.h"

#include "board.h"
#include "driver/gpio.h"
#include "esp_timer.h"
#include "lvgl.h"

#define POLL_MS     20
#define LONG_MS     800

static void (*s_cb)(bool long_press);
static int64_t s_down_ms = -1;
static bool s_long_fired;

static void poll_cb(lv_timer_t *t)
{
    bool pressed = gpio_get_level(PIN_BOOT_BTN) == 0;
    int64_t now = esp_timer_get_time() / 1000;
    if (pressed) {
        if (s_down_ms < 0) {
            s_down_ms = now;
            s_long_fired = false;
        } else if (!s_long_fired && now - s_down_ms >= LONG_MS) {
            s_long_fired = true;
            s_cb(true);
        }
    } else if (s_down_ms >= 0) {
        if (!s_long_fired && now - s_down_ms >= 2 * POLL_MS) {
            s_cb(false);
        }
        s_down_ms = -1;
    }
}

void button_init(void (*cb)(bool long_press))
{
    s_cb = cb;
    gpio_config_t io = {
        .pin_bit_mask = 1ULL << PIN_BOOT_BTN,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&io);
    lv_timer_create(poll_cb, POLL_MS, NULL);
}
