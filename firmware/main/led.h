#pragma once
// Onboard WS2812 used as an ambient "needs you" signal.
#include <stdint.h>

typedef enum {
    LED_OFF = 0,
    LED_ALERT,         // slow orange breathing
    LED_ALERT_URGENT,  // fast orange/red blink
    LED_ERROR,         // dim steady red
    LED_DONE,          // soft green
    LED_LIMIT,         // slow dim purple: plan limit reached
    LED_DONE_AWAY,     // slow green breathing: finished while you were away (D49)
} led_mode_t;

void led_init(void);
void led_set_mode(led_mode_t mode);
// One-shot overlay on top of the mode: `count` flashes of 0xRRGGBB (D47 low limit).
void led_pulse(uint32_t rgb, int count);
const char *led_mode_name(void);
