#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cJSON.h"

typedef enum {
    PROTO_SRC_USB = 0,
    PROTO_SRC_UDP = 1,
} proto_src_t;

typedef void (*proto_handler_t)(const cJSON *msg, proto_src_t src);

void proto_init(void);

// Called from link RX tasks: parses the line and queues it for the LVGL task.
void proto_feed_line(const char *line, size_t len, proto_src_t src);

// Called on the LVGL task: dispatches every queued message to the registered handler.
void proto_poll(void);

// Handler for message types other than "cmd" (snapshots, heartbeats).
void proto_set_handler(proto_handler_t handler);

// Send a line back to wherever `src` points (USB, or the last UDP peer).
void proto_reply(proto_src_t src, const char *line, size_t len);
void proto_set_udp_sender(void (*send)(const char *line, size_t len));

// Send an unsolicited line (button events) on every link that has a peer.
void proto_send_all(const char *line, size_t len);

// The link that most recently delivered a valid message ("usb", "wifi" or "none").
const char *proto_active_link(void);
int64_t proto_last_rx_us(void);
