#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Newline-delimited JSON over the chip's USB-Serial/JTAG port.
// Incoming lines are handed to proto_feed_line() from the link's RX task.
void link_usb_init(void);

// Send one complete line. timeout_ms == 0 drops the line if the TX buffer is full
// (acks/stats); a positive timeout waits for the host to drain (screenshots).
bool link_usb_send(const char *line, size_t len, uint32_t timeout_ms);

// True when a host has polled the port recently (USB SOF seen).
bool link_usb_connected(void);
