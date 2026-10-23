"""Firmware update over WiFi (decision D37): the device pulls the image from a one-shot
TCP listener that exists only for the duration of one transfer.

  1. listen on the Mac's LAN address (random port), only for the display's IP
  2. send {"t":"cmd","c":"ota","ip","port","len","sha"} (token-authenticated over UDP)
  3. stream the image; the device checks length + SHA-256, then reboots into it
  4. wait for the device to come back with the new build
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import time
from pathlib import Path

from .protocol import encode

log = logging.getLogger("claude_display.ota")

READY_TIMEOUT_S = 30
CONNECT_TIMEOUT_S = 15
TRANSFER_TIMEOUT_S = 120
REBOOT_TIMEOUT_S = 40
ESP_IMAGE_MAGIC = 0xE9


class OtaError(Exception):
    pass


def _local_ip_towards(ip: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 9))  # no packet is sent; this only picks the route
        return s.getsockname()[0]
    finally:
        s.close()


async def push(bridge, path: str, via: str = "wifi") -> dict:
    data = Path(path).read_bytes()
    if not data or data[0] != ESP_IMAGE_MAGIC:
        raise OtaError("not an ESP-IDF app image")
    wifi = next((l for l in bridge.links if l.name == "wifi"), None)
    dev_ip = wifi.summary()["addr"] if wifi else None
    if not dev_ip:
        raise OtaError("display not reachable over WiFi")
    if via == "wifi" and not wifi.connected:
        raise OtaError("WiFi link is down")
    # Right after a reboot the display may not have rejoined the network yet; a lost UDP
    # command would look like "device never connected". Wait for a WiFi pong first.
    t_ping = time.monotonic()
    while wifi.last_rx < t_ping:
        if time.monotonic() - t_ping > READY_TIMEOUT_S:
            raise OtaError("display does not answer over WiFi")
        wifi.send(encode({"t": "cmd", "c": "ping"}))
        await asyncio.sleep(1)
    local_ip = _local_ip_towards(dev_ip)
    sha = hashlib.sha256(data).hexdigest()
    before = dict(await bridge.device_stats_now())

    events: asyncio.Queue = asyncio.Queue()
    bridge.ota_events = events
    served = asyncio.get_running_loop().create_future()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")[0]
        if peer != dev_ip or served.done():
            writer.close()
            return
        t0 = time.monotonic()
        try:
            writer.write(data)
            await writer.drain()
            served.set_result(time.monotonic() - t0)
        except (ConnectionError, OSError) as e:
            served.set_exception(OtaError(f"send failed: {e}"))
        finally:
            writer.close()

    server = await asyncio.start_server(serve, local_ip, 0)
    port = server.sockets[0].getsockname()[1]
    t_start = time.monotonic()
    try:
        cmd = {"t": "cmd", "c": "ota", "ip": local_ip, "port": port, "len": len(data), "sha": sha}
        sent = (wifi.send(encode(cmd)) and "wifi") if via == "wifi" else bridge._send(cmd)
        if not sent or sent == "none":
            raise OtaError("could not send the update command")
        log.info("ota: %d bytes via %s, device fetches from %s:%d", len(data), sent, local_ip, port)
        pcts: list[int] = []
        deadline = time.monotonic() + TRANSFER_TIMEOUT_S
        connect_by = time.monotonic() + CONNECT_TIMEOUT_S
        while True:
            if not served.done() and time.monotonic() > connect_by and not pcts:
                raise OtaError("device never connected")
            if time.monotonic() > deadline:
                raise OtaError("timed out")
            try:
                ev = await asyncio.wait_for(events.get(), timeout=1)
            except asyncio.TimeoutError:
                continue
            if "err" in ev:
                raise OtaError(f"device: {ev['err']}")
            if "pct" in ev:
                pcts.append(int(ev["pct"]))
            if ev.get("ok"):
                heap_min_during = ev.get("heap_min")
                break
        transfer_s = time.monotonic() - t_start
    finally:
        server.close()
        bridge.ota_events = None

    # The device reboots into the new image; wait until it reports in again.
    up_before = before.get("up", 1 << 30)
    after: dict = {}
    t_reboot = time.monotonic()
    await asyncio.sleep(2)
    while time.monotonic() - t_reboot < REBOOT_TIMEOUT_S:
        bridge.device_stats = {}
        bridge._send({"t": "cmd", "c": "stats"})
        await asyncio.sleep(1)
        st = bridge.device_stats
        if st.get("ver") and st.get("up", up_before) < up_before:
            after = dict(st)
            break
    return {
        "ok": bool(after),
        "bytes": len(data),
        "sha256": sha,
        "transfer_s": round(transfer_s, 1),
        "progress": pcts,
        "ver_before": before.get("ver"),
        "ver_after": after.get("ver"),
        "heap_min_during": heap_min_during,
        "error": None if after else "device did not come back after reboot",
    }
