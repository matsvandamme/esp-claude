#include "shot.h"

#include <stdio.h>

#include "board.h"
#include "display.h"
#include "link_usb.h"
#include "mbedtls/base64.h"
#include "stats.h"

static bool s_active;

bool shot_active(void)
{
    return s_active;
}

void shot_request(void)
{
    s_active = true;
    lv_obj_invalidate(lv_screen_active());
    lv_obj_invalidate(lv_layer_top());
    lv_refr_now(display_get());
    s_active = false;
    stats_reset();
}

void shot_emit_area(int32_t x, int32_t y, int32_t w, int32_t h, const uint16_t *px)
{
    // One row per line keeps each line well under the host's buffer and the TX ring.
    static char line[64 + (LCD_H_RES * 2 * 4) / 3 + 8];
    for (int32_t r = 0; r < h; r++) {
        int n = snprintf(line, sizeof(line), "{\"t\":\"shot\",\"x\":%ld,\"y\":%ld,\"w\":%ld,\"d\":\"", (long)x,
                         (long)(y + r), (long)w);
        size_t olen = 0;
        mbedtls_base64_encode((unsigned char *)line + n, sizeof(line) - n - 4, &olen,
                              (const unsigned char *)(px + r * w), (size_t)w * 2);
        n += (int)olen;
        line[n++] = '"';
        line[n++] = '}';
        line[n++] = '\n';
        link_usb_send(line, (size_t)n, 200);
    }
}

void shot_end(void)
{
    char line[64];
    int n = snprintf(line, sizeof(line), "{\"t\":\"shot_end\",\"w\":%d,\"h\":%d}\n", LCD_H_RES, LCD_V_RES);
    link_usb_send(line, (size_t)n, 200);
}
