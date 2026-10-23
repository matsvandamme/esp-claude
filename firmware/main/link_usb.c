#include "link_usb.h"

#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "proto.h"

#define RX_LINE_MAX 2048

static SemaphoreHandle_t s_tx_lock;

static void rx_task(void *arg)
{
    static char line[RX_LINE_MAX];
    size_t n = 0;
    bool overflow = false;
    uint8_t chunk[256];
    for (;;) {
        int got = usb_serial_jtag_read_bytes(chunk, sizeof(chunk), portMAX_DELAY);
        for (int i = 0; i < got; i++) {
            char c = (char)chunk[i];
            if (c == '\n' || c == '\r') {
                if (n > 0 && !overflow) {
                    line[n] = 0;
                    proto_feed_line(line, n, PROTO_SRC_USB);
                }
                n = 0;
                overflow = false;
            } else if (n < RX_LINE_MAX - 1) {
                line[n++] = c;
            } else {
                overflow = true;
            }
        }
    }
}

void link_usb_init(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = 4096,
        .tx_buffer_size = 8192,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    // Route stdout/ESP_LOG through the same driver so writes never race the hardware FIFO.
    usb_serial_jtag_vfs_use_driver();
    s_tx_lock = xSemaphoreCreateMutex();
    xTaskCreate(rx_task, "usb_rx", 4096, NULL, 6, NULL);
}

bool link_usb_connected(void)
{
    return usb_serial_jtag_is_connected();
}

bool link_usb_send(const char *line, size_t len, uint32_t timeout_ms)
{
    if (!usb_serial_jtag_is_connected()) {
        return false;
    }
    if (xSemaphoreTake(s_tx_lock, pdMS_TO_TICKS(timeout_ms ? timeout_ms : 2)) != pdTRUE) {
        return false;
    }
    bool ok = true;
    size_t off = 0;
    TickType_t ticks = pdMS_TO_TICKS(timeout_ms);
    while (off < len) {
        int w = usb_serial_jtag_write_bytes(line + off, len - off, ticks);
        if (w <= 0) {
            ok = false;
            break;
        }
        off += (size_t)w;
    }
    xSemaphoreGive(s_tx_lock);
    return ok;
}
