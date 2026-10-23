#include "ota.h"

#include <stdio.h>
#include <errno.h>
#include <string.h>

#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "mbedtls/sha256.h"

#define CHUNK        4096
#define IO_TIMEOUT_S 10
#define TASK_STACK   6144

typedef struct {
    uint32_t ip_be;
    uint16_t port;
    uint32_t len;
    uint8_t sha[32];
    proto_src_t src;
} job_t;

static job_t s_job;
static volatile int s_progress = -1;
static bool s_marked;

static void reply(const char *fmt, const char *detail, int pct)
{
    char buf[96];
    int n = detail ? snprintf(buf, sizeof(buf), fmt, detail) : snprintf(buf, sizeof(buf), fmt, pct);
    proto_reply(s_job.src, buf, (size_t)n);
}

static void fail(const char *why)
{
    reply("{\"t\":\"ota\",\"err\":\"%s\"}\n", why, 0);
}

static bool parse_sha(const char *hex, uint8_t out[32])
{
    if (!hex || strlen(hex) != 64) {
        return false;
    }
    for (int i = 0; i < 32; i++) {
        unsigned v;
        if (sscanf(hex + 2 * i, "%2x", &v) != 1) {
            return false;
        }
        out[i] = (uint8_t)v;
    }
    return true;
}

static const char *transfer(int sock, uint8_t *buf)
{
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (!part) {
        return "no partition";
    }
    if (s_job.len > part->size) {
        return "image too large";
    }
    esp_ota_handle_t h;
    // Sequential mode erases sector by sector as data arrives, so the sender never sits
    // behind a multi-second up-front erase of the whole slot.
    if (esp_ota_begin(part, OTA_WITH_SEQUENTIAL_WRITES, &h) != ESP_OK) {
        return "begin failed";
    }
    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);
    uint32_t got = 0;
    int last_pct = -1;
    const char *err = NULL;
    while (got < s_job.len) {
        size_t want = s_job.len - got < CHUNK ? s_job.len - got : CHUNK;
        int n = recv(sock, buf, want, 0);
        if (n <= 0) {
            static char why[40];
            snprintf(why, sizeof(why), "connection lost at %lu (%s)", (unsigned long)got,
                     n == 0 ? "closed" : (errno == EAGAIN ? "timeout" : "error"));
            err = why;
            break;
        }
        mbedtls_sha256_update(&sha, buf, (size_t)n);
        if (esp_ota_write(h, buf, (size_t)n) != ESP_OK) {
            err = "write failed";
            break;
        }
        got += (uint32_t)n;
        int pct = (int)((uint64_t)got * 100 / s_job.len);
        s_progress = pct;
        if (pct / 10 != last_pct / 10) {
            last_pct = pct;
            reply("{\"t\":\"ota\",\"pct\":%d}\n", NULL, pct);
        }
    }
    uint8_t digest[32];
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);
    if (!err && memcmp(digest, s_job.sha, 32) != 0) {
        err = "sha256 mismatch";
    }
    if (err) {
        esp_ota_abort(h);
        return err;
    }
    if (esp_ota_end(h) != ESP_OK) {  // also validates the image header and checksum
        return "image invalid";
    }
    if (esp_ota_set_boot_partition(part) != ESP_OK) {
        return "set boot failed";
    }
    return NULL;
}

static void ota_task(void *arg)
{
    const char *err = NULL;
    uint8_t *buf = malloc(CHUNK);
    int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (!buf || sock < 0) {
        err = "no memory";
    } else {
        struct timeval tv = {.tv_sec = IO_TIMEOUT_S};
        setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
        struct sockaddr_in addr = {.sin_family = AF_INET, .sin_port = htons(s_job.port)};
        addr.sin_addr.s_addr = s_job.ip_be;
        if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
            err = "connect failed";
        } else {
            err = transfer(sock, buf);
        }
    }
    if (sock >= 0) {
        close(sock);
    }
    free(buf);
    if (err) {
        fail(err);
        s_progress = -1;
    } else {
        // heap_min here is the low-water mark including the transfer.
        reply("{\"t\":\"ota\",\"ok\":1,\"heap_min\":%d}\n", NULL,
              (int)heap_caps_get_minimum_free_size(MALLOC_CAP_8BIT));
        vTaskDelay(pdMS_TO_TICKS(400));  // let the reply and the last frame out
        esp_restart();
    }
    vTaskDelete(NULL);
}

bool ota_start(uint32_t ip_be, uint16_t port, uint32_t len, const char *sha_hex, proto_src_t src)
{
    s_job.src = src;
    if (s_progress >= 0) {
        fail("busy");
        return false;
    }
    if (!ip_be || !port || !len || !parse_sha(sha_hex, s_job.sha)) {
        fail("bad request");
        return false;
    }
    s_job.ip_be = ip_be;
    s_job.port = port;
    s_job.len = len;
    s_progress = 0;
    if (xTaskCreate(ota_task, "ota", TASK_STACK, NULL, 4, NULL) != pdPASS) {
        s_progress = -1;
        fail("no memory");
        return false;
    }
    return true;
}

int ota_progress(void)
{
    return s_progress;
}

void ota_mark_valid(void)
{
    if (s_marked) {
        return;
    }
    s_marked = true;
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) == ESP_OK &&
        st == ESP_OTA_IMG_PENDING_VERIFY) {
        esp_ota_mark_app_valid_cancel_rollback();
    }
}

const char *ota_running_label(void)
{
    static char buf[48];
    if (!buf[0]) {
        snprintf(buf, sizeof(buf), "%s@%s", esp_app_get_description()->version, esp_ota_get_running_partition()->label);
    }
    return buf;
}
