#pragma once
#include <stdint.h>

// WiFi fallback link (decision D1): station on the SSID stored in the `creds` NVS partition,
// mDNS name claude-display.local, newline-delimited JSON over UDP port 47322. Every datagram
// must carry the shared token ("k":"...") or it is dropped (D16).
void link_wifi_init(void);

// "unprovisioned", "connecting", "no_ap", "auth_fail" or "ip"
const char *link_wifi_state(void);
int8_t link_wifi_rssi(void);
uint32_t link_wifi_drops(void);

// IPv4 address (network byte order) of the last peer that sent a valid datagram, 0 if none.
uint32_t link_wifi_peer_ip(void);
