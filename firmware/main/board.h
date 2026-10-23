#pragma once
// Waveshare ESP32-C6-LCD-1.47 pin map and panel geometry.

#define PIN_LCD_MOSI   6
#define PIN_LCD_SCLK   7
#define PIN_LCD_CS     14
#define PIN_LCD_DC     15
#define PIN_LCD_RST    21
#define PIN_LCD_BL     22
#define PIN_RGB_LED    8
#define PIN_BOOT_BTN   9

#define LCD_SPI_HOST   SPI2_HOST
#define LCD_PCLK_HZ    (80 * 1000 * 1000)

// Landscape: the 172-px panel sits at column offset 34 of the ST7789's 240-px RAM,
// which becomes the y gap once X/Y are swapped.
#define LCD_H_RES      320
#define LCD_V_RES      172
#define LCD_X_GAP      0
#define LCD_Y_GAP      34
#define LCD_SWAP_XY    true
// Rotated 180° (upright with USB-C on the left, D42): both mirrors flipped. The 34-px offset is the same
// on either side of the 240-px RAM (34 + 172 + 34), so the gap doesn't change.
#define LCD_MIRROR_X   false
#define LCD_MIRROR_Y   true

#define LCD_BUF_LINES  24
