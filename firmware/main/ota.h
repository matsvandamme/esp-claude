#pragma once
// Firmware update over WiFi (decision D37). The bridge sends a token-authenticated
// {"t":"cmd","c":"ota","ip":…,"port":…,"len":…,"sha":…}; the device connects back to that
// address, streams the image into the inactive OTA slot, checks length and SHA-256, and
// reboots into it. The new image only becomes permanent once it has shown a snapshot from
// the bridge (ota_mark_valid); a crash or hang before that boots the previous image again.
#include <stdbool.h>
#include <stdint.h>

#include "proto.h"

// Starts the transfer task. Returns false (and replies with an error) if one is running or
// the request is malformed.
bool ota_start(uint32_t ip_be, uint16_t port, uint32_t len, const char *sha_hex, proto_src_t src);

// -1 when idle, else 0..100.
int ota_progress(void);

// Call once the app has proven it works; cancels the pending rollback.
void ota_mark_valid(void);

// "ota_0"/"ota_1" plus the app version, for stats.
const char *ota_running_label(void);
