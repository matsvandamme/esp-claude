#include "proto.h"

#include <stdio.h>
#include <string.h>

#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "link_usb.h"
#include "link_wifi.h"
#include "lwip/inet.h"
#include "ota.h"
#include "shot.h"
#include "stats.h"

typedef struct {
    cJSON *msg;
    proto_src_t src;
} queued_t;

static QueueHandle_t s_queue;
static proto_handler_t s_handler;
static void (*s_udp_send)(const char *, size_t);
static volatile int64_t s_last_rx_us;
static volatile int s_last_src = -1;

void proto_init(void)
{
    s_queue = xQueueCreate(16, sizeof(queued_t));
}

void proto_set_handler(proto_handler_t handler)
{
    s_handler = handler;
}

void proto_set_udp_sender(void (*send)(const char *line, size_t len))
{
    s_udp_send = send;
}

const char *proto_active_link(void)
{
    if (s_last_src < 0 || esp_timer_get_time() - s_last_rx_us > 5000000) {
        return "none";
    }
    return s_last_src == PROTO_SRC_UDP ? "wifi" : "usb";
}

int64_t proto_last_rx_us(void)
{
    return s_last_rx_us;
}

void proto_feed_line(const char *line, size_t len, proto_src_t src)
{
    if (len == 0 || line[0] != '{') {
        return;
    }
    cJSON *msg = cJSON_ParseWithLength(line, len);
    if (!msg) {
        return;
    }
    queued_t q = {.msg = msg, .src = src};
    if (xQueueSend(s_queue, &q, 0) != pdTRUE) {
        // UI is behind: drop the oldest message rather than block the link.
        queued_t old;
        if (xQueueReceive(s_queue, &old, 0) == pdTRUE) {
            cJSON_Delete(old.msg);
        }
        if (xQueueSend(s_queue, &q, 0) != pdTRUE) {
            cJSON_Delete(msg);
        }
    }
}

void proto_reply(proto_src_t src, const char *line, size_t len)
{
    if (src == PROTO_SRC_UDP) {
        if (s_udp_send) {
            s_udp_send(line, len);
        }
    } else {
        link_usb_send(line, len, 0);
    }
}

void proto_send_all(const char *line, size_t len)
{
    link_usb_send(line, len, 0);
    if (s_udp_send) {
        s_udp_send(line, len);
    }
}

static void handle_cmd(const cJSON *msg, proto_src_t src)
{
    const cJSON *c = cJSON_GetObjectItemCaseSensitive(msg, "c");
    if (!cJSON_IsString(c)) {
        return;
    }
    char buf[400];
    if (strcmp(c->valuestring, "stats") == 0) {
        int n = stats_report_json(buf, sizeof(buf), proto_active_link());
        proto_reply(src, buf, (size_t)n);
    } else if (strcmp(c->valuestring, "shot") == 0) {
        if (src == PROTO_SRC_USB) {
            shot_request();
        }
    } else if (strcmp(c->valuestring, "ota") == 0) {
        const cJSON *ip = cJSON_GetObjectItemCaseSensitive(msg, "ip");
        const cJSON *port = cJSON_GetObjectItemCaseSensitive(msg, "port");
        const cJSON *len = cJSON_GetObjectItemCaseSensitive(msg, "len");
        const cJSON *sha = cJSON_GetObjectItemCaseSensitive(msg, "sha");
        uint32_t addr = cJSON_IsString(ip) ? inet_addr(ip->valuestring) : 0;
        if (addr == IPADDR_NONE) {
            addr = 0;
        }
        // Over WiFi, only fetch from the machine that sent the (token-checked) command.
        if (src == PROTO_SRC_UDP && addr != link_wifi_peer_ip()) {
            addr = 0;
        }
        ota_start(addr, cJSON_IsNumber(port) ? (uint16_t)port->valueint : 0,
                  cJSON_IsNumber(len) ? (uint32_t)len->valuedouble : 0, cJSON_IsString(sha) ? sha->valuestring : NULL,
                  src);
    } else if (strcmp(c->valuestring, "ping") == 0) {
        int n = snprintf(buf, sizeof(buf), "{\"t\":\"pong\",\"up\":%lld}\n", esp_timer_get_time() / 1000);
        proto_reply(src, buf, (size_t)n);
    }
}

void proto_poll(void)
{
    queued_t q;
    while (xQueueReceive(s_queue, &q, 0) == pdTRUE) {
        s_last_rx_us = esp_timer_get_time();
        s_last_src = (int)q.src;
        const cJSON *t = cJSON_GetObjectItemCaseSensitive(q.msg, "t");
        if (cJSON_IsString(t) && strcmp(t->valuestring, "cmd") == 0) {
            handle_cmd(q.msg, q.src);
        } else if (s_handler) {
            s_handler(q.msg, q.src);
        }
        cJSON_Delete(q.msg);
    }
}
