#include "link_wifi.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "mdns.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "proto.h"

#define UDP_PORT      47322
#define HOSTNAME      "claude-display"
#define MAX_DGRAM     1500

static const char *TAG = "wifi";

static char s_token_pat[64];  // "\"k\":\"<token>\""
static volatile const char *s_state = "unprovisioned";
static volatile uint32_t s_drops;
static int s_sock = -1;
static struct sockaddr_in s_peer;
static volatile bool s_have_peer;
static uint32_t s_backoff_ms = 1000;
static esp_timer_handle_t s_retry_timer;

static void retry_cb(void *arg)
{
    esp_wifi_connect();
}

const char *link_wifi_state(void)
{
    return (const char *)s_state;
}

uint32_t link_wifi_drops(void)
{
    return s_drops;
}

int8_t link_wifi_rssi(void)
{
    wifi_ap_record_t ap;
    return esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}

static void udp_send(const char *line, size_t len)
{
    if (s_sock >= 0 && s_have_peer) {
        sendto(s_sock, line, len, 0, (struct sockaddr *)&s_peer, sizeof(s_peer));
    }
}

static void udp_task(void *arg)
{
    static char buf[MAX_DGRAM + 1];
    s_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons(UDP_PORT), .sin_addr.s_addr = htonl(INADDR_ANY)};
    if (s_sock < 0 || bind(s_sock, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        ESP_LOGE(TAG, "udp bind failed");
        vTaskDelete(NULL);
        return;
    }
    proto_set_udp_sender(udp_send);
    for (;;) {
        struct sockaddr_in from;
        socklen_t flen = sizeof(from);
        int n = recvfrom(s_sock, buf, MAX_DGRAM, 0, (struct sockaddr *)&from, &flen);
        if (n <= 0) {
            continue;
        }
        buf[n] = 0;
        if (!strstr(buf, s_token_pat)) {
            s_drops++;
            continue;
        }
        s_peer = from;
        s_have_peer = true;
        // A datagram may hold several lines.
        char *line = buf;
        while (line && *line) {
            char *nl = strchr(line, '\n');
            size_t len = nl ? (size_t)(nl - line) : strlen(line);
            if (len) {
                proto_feed_line(line, len, PROTO_SRC_UDP);
            }
            line = nl ? nl + 1 : NULL;
        }
    }
}

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
        s_backoff_ms = 1000;
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        wifi_event_sta_disconnected_t *d = data;
        if (d->reason == WIFI_REASON_NO_AP_FOUND || d->reason == WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY ||
            d->reason == WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD || d->reason == WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD) {
            s_state = "no_ap";
        } else if (d->reason == WIFI_REASON_AUTH_FAIL || d->reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
                   d->reason == WIFI_REASON_HANDSHAKE_TIMEOUT) {
            s_state = "auth_fail";
        } else {
            s_state = "connecting";
        }
        ESP_LOGW(TAG, "disconnected (reason %d), retry in %lu ms", d->reason, (unsigned long)s_backoff_ms);
        // Never block the event loop: retry from a one-shot timer.
        esp_timer_stop(s_retry_timer);
        esp_timer_start_once(s_retry_timer, (uint64_t)s_backoff_ms * 1000);
        if (s_backoff_ms < 30000) {
            s_backoff_ms *= 2;
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_state = "ip";
        // Modem sleep adds 100-300 ms to every received datagram; the display is mains-powered.
        esp_wifi_set_ps(WIFI_PS_NONE);
    }
}

static bool load_creds(wifi_config_t *cfg)
{
    if (nvs_flash_init_partition("creds") != ESP_OK) {
        return false;
    }
    nvs_handle_t h;
    if (nvs_open_from_partition("creds", "wifi", NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t n = sizeof(cfg->sta.ssid);
    bool ok = nvs_get_str(h, "ssid", (char *)cfg->sta.ssid, &n) == ESP_OK;
    n = sizeof(cfg->sta.password);
    ok = ok && nvs_get_str(h, "pass", (char *)cfg->sta.password, &n) == ESP_OK;
    char token[40];
    n = sizeof(token);
    ok = ok && nvs_get_str(h, "token", token, &n) == ESP_OK;
    nvs_close(h);
    if (ok) {
        snprintf(s_token_pat, sizeof(s_token_pat), "\"k\":\"%s\"", token);
    }
    return ok;
}

void link_wifi_init(void)
{
    wifi_config_t cfg = {0};
    if (!load_creds(&cfg)) {
        s_state = "unprovisioned";
        ESP_LOGW(TAG, "no credentials in the creds partition; WiFi link disabled");
        return;
    }
    cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    cfg.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;
    cfg.sta.pmf_cfg.capable = true;
    s_state = "connecting";
    esp_timer_create_args_t targs = {.callback = retry_cb, .name = "wifi_retry"};
    ESP_ERROR_CHECK(esp_timer_create(&targs, &s_retry_timer));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &cfg));
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    if (netif) {
        esp_netif_set_hostname(netif, HOSTNAME);
    }
    if (mdns_init() == ESP_OK) {
        mdns_hostname_set(HOSTNAME);
        mdns_instance_name_set("Claude Code status display");
        mdns_service_add(NULL, "_claude-display", "_udp", UDP_PORT, NULL, 0);
    }
    xTaskCreate(udp_task, "udp", 4096, NULL, 5, NULL);
    esp_wifi_connect();
}

uint32_t link_wifi_peer_ip(void)
{
    return s_peer.sin_addr.s_addr;
}
