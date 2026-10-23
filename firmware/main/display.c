#include "display.h"

#include "board.h"
#include "driver/gpio.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_st7789.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "shot.h"
#include "stats.h"

static const char *TAG = "display";

static esp_lcd_panel_handle_t s_panel;
static lv_display_t *s_disp;
static uint8_t s_backlight = 0;
static volatile bool s_last_in_flight;

static bool on_color_trans_done(esp_lcd_panel_io_handle_t io, esp_lcd_panel_io_event_data_t *edata,
                                          void *user_ctx)
{
    if (s_last_in_flight) {
        s_last_in_flight = false;
        stats_frame_flushed();
    }
    lv_display_flush_ready((lv_display_t *)user_ctx);
    return false;
}

static void flush_cb(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
    int32_t w = lv_area_get_width(area);
    int32_t h = lv_area_get_height(area);
    if (shot_active()) {
        shot_emit_area(area->x1, area->y1, w, h, (const uint16_t *)px_map);
    }
    // LVGL renders little-endian RGB565; the panel expects big-endian.
    lv_draw_sw_rgb565_swap(px_map, (uint32_t)(w * h));
    if (lv_display_flush_is_last(disp)) {
        s_last_in_flight = true;
        if (shot_active()) {
            shot_end();
        }
    }
    esp_lcd_panel_draw_bitmap(s_panel, area->x1, area->y1, area->x2 + 1, area->y2 + 1, px_map);
}

static void render_start_cb(lv_event_t *e)
{
    stats_frame_started();
}

static uint32_t tick_cb(void)
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}

static void backlight_init(void)
{
    ledc_timer_config_t t = {
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .duty_resolution = LEDC_TIMER_10_BIT,
        .timer_num = LEDC_TIMER_0,
        .freq_hz = 20000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&t));
    ledc_channel_config_t c = {
        .gpio_num = PIN_LCD_BL,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0,
        .timer_sel = LEDC_TIMER_0,
        .duty = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&c));
}

void display_set_backlight(uint8_t percent)
{
    if (percent > 100) {
        percent = 100;
    }
    s_backlight = percent;
    // Perceptual curve: squared.
    uint32_t duty = (uint32_t)percent * percent * 1023 / 10000;
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0, duty);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0);
}

uint8_t display_get_backlight(void)
{
    return s_backlight;
}

lv_display_t *display_get(void)
{
    return s_disp;
}

void display_init(void)
{
    backlight_init();

    spi_bus_config_t bus = {
        .sclk_io_num = PIN_LCD_SCLK,
        .mosi_io_num = PIN_LCD_MOSI,
        .miso_io_num = -1,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = LCD_H_RES * LCD_BUF_LINES * 2,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(LCD_SPI_HOST, &bus, SPI_DMA_CH_AUTO));

    lv_init();
    lv_tick_set_cb(tick_cb);
    s_disp = lv_display_create(LCD_H_RES, LCD_V_RES);

    esp_lcd_panel_io_handle_t io;
    esp_lcd_panel_io_spi_config_t io_cfg = {
        .dc_gpio_num = PIN_LCD_DC,
        .cs_gpio_num = PIN_LCD_CS,
        .pclk_hz = LCD_PCLK_HZ,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
        .spi_mode = 0,
        .trans_queue_depth = 10,
        .on_color_trans_done = on_color_trans_done,
        .user_ctx = s_disp,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)LCD_SPI_HOST, &io_cfg, &io));

    esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = PIN_LCD_RST,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st7789(io, &panel_cfg, &s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(s_panel));
    ESP_ERROR_CHECK(esp_lcd_panel_invert_color(s_panel, true));
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(s_panel, LCD_SWAP_XY));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(s_panel, LCD_MIRROR_X, LCD_MIRROR_Y));
    ESP_ERROR_CHECK(esp_lcd_panel_set_gap(s_panel, LCD_X_GAP, LCD_Y_GAP));

    size_t buf_bytes = LCD_H_RES * LCD_BUF_LINES * 2;
    void *buf1 = heap_caps_malloc(buf_bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    void *buf2 = heap_caps_malloc(buf_bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    assert(buf1 && buf2);
    lv_display_set_color_format(s_disp, LV_COLOR_FORMAT_RGB565);
    lv_display_set_buffers(s_disp, buf1, buf2, buf_bytes, LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(s_disp, flush_cb);
    lv_display_add_event_cb(s_disp, render_start_cb, LV_EVENT_RENDER_START, NULL);

    // Paint black before the backlight comes on so the panel never flashes garbage.
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_black(), 0);
    lv_refr_now(s_disp);
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(s_panel, true));
    ESP_LOGW(TAG, "display up %dx%d @%d MHz", LCD_H_RES, LCD_V_RES, LCD_PCLK_HZ / 1000000);
}

void display_run(void (*on_tick)(void))
{
    for (;;) {
        if (on_tick) {
            on_tick();
        }
        uint32_t wait_ms = lv_timer_handler();
        if (wait_ms > 5) {
            wait_ms = 5;
        }
        vTaskDelay(pdMS_TO_TICKS(wait_ms ? wait_ms : 1));
    }
}
