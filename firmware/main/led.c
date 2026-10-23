#include "led.h"

#include "board.h"
#include "esp_timer.h"
#include "led_strip.h"

#define LED_PERIOD_US 20000
#define LED_MAX       60  // of 255: the WS2812 is very bright at arm's length

static led_strip_handle_t s_strip;
static volatile led_mode_t s_mode = LED_OFF;
static uint8_t s_r, s_g, s_b;
static volatile int64_t s_pulse_end_ms;  // overlay active until then
static volatile uint32_t s_pulse_rgb;
#define PULSE_MS 600  // one flash: 350 ms on, 250 ms off

static void put(uint8_t r, uint8_t g, uint8_t b)
{
    if (r == s_r && g == s_g && b == s_b) {
        return;
    }
    s_r = r;
    s_g = g;
    s_b = b;
    led_strip_set_pixel(s_strip, 0, r, g, b);
    led_strip_refresh(s_strip);
}

static uint8_t scale(uint8_t c, uint32_t level)  // level 0..1000
{
    return (uint8_t)((uint32_t)c * LED_MAX / 255 * level / 1000);
}

static void tick(void *arg)
{
    int64_t t = esp_timer_get_time() / 1000;
    if (t < s_pulse_end_ms) {
        uint32_t c = s_pulse_rgb;
        bool on = (s_pulse_end_ms - t) % PULSE_MS >= PULSE_MS - 350;
        uint32_t lvl = on ? 1000 : 0;
        put(scale(c >> 16, lvl), scale((c >> 8) & 0xFF, lvl), scale(c & 0xFF, lvl));
        return;
    }
    switch (s_mode) {
    case LED_OFF:
        put(0, 0, 0);
        break;
    case LED_ALERT: {
        // Triangle wave, 1.6 s period, never fully dark.
        uint32_t ph = (uint32_t)(t % 1600);
        uint32_t lvl = 150 + (ph < 800 ? ph : 1600 - ph) * 850 / 800;
        put(scale(215, lvl), scale(119, lvl), scale(87, lvl));
        break;
    }
    case LED_ALERT_URGENT:
        if ((t / 150) % 2) {
            put(scale(255, 1000), scale(80, 1000), 0);
        } else {
            put(scale(255, 400), 0, 0);
        }
        break;
    case LED_ERROR:
        put(scale(255, 350), scale(30, 350), scale(40, 350));
        break;
    case LED_DONE:
        put(scale(78, 700), scale(186, 700), scale(101, 700));
        break;
    case LED_LIMIT: {
        // Slow 4 s breathing between 5 % and 35 %: present, not demanding.
        uint32_t ph = (uint32_t)(t % 4000);
        uint32_t lvl = 50 + (ph < 2000 ? ph : 4000 - ph) * 300 / 2000;
        put(scale(177, lvl), scale(185, lvl), scale(249, lvl));
        break;
    }
    case LED_DONE_AWAY: {
        // 3 s green breathing between 10 % and 70 %: something to come back to.
        uint32_t ph = (uint32_t)(t % 3000);
        uint32_t lvl = 100 + (ph < 1500 ? ph : 3000 - ph) * 600 / 1500;
        put(scale(78, lvl), scale(186, lvl), scale(101, lvl));
        break;
    }
    }
}

void led_init(void)
{
    led_strip_config_t cfg = {
        .strip_gpio_num = PIN_RGB_LED,
        .max_leds = 1,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
    };
    led_strip_rmt_config_t rmt = {
        .resolution_hz = 10 * 1000 * 1000,
    };
    ESP_ERROR_CHECK(led_strip_new_rmt_device(&cfg, &rmt, &s_strip));
    led_strip_clear(s_strip);
    esp_timer_handle_t timer;
    esp_timer_create_args_t args = {.callback = tick, .name = "led"};
    ESP_ERROR_CHECK(esp_timer_create(&args, &timer));
    ESP_ERROR_CHECK(esp_timer_start_periodic(timer, LED_PERIOD_US));
}

void led_set_mode(led_mode_t mode)
{
    s_mode = mode;
}

void led_pulse(uint32_t rgb, int count)
{
    s_pulse_rgb = rgb;
    s_pulse_end_ms = esp_timer_get_time() / 1000 + (int64_t)count * PULSE_MS;
}

const char *led_mode_name(void)
{
    static const char *names[] = {"off", "alert", "alert_urgent", "error", "done", "limit", "done_away"};
    if (esp_timer_get_time() / 1000 < s_pulse_end_ms) {
        return "pulse";
    }
    return names[s_mode];
}
