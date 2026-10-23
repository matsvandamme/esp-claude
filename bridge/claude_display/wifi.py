"""WiFi (UDP) link to the display (decisions D1, D16, D22).

`connected` means "address resolved", not "heard from the device": the device only learns
where to reply once it receives a token-bearing datagram, so waiting for it first would
deadlock. Acks are used for health, and the address is re-resolved after missed acks.
"""
from __future__ import annotations

import errno
import logging
import socket
import threading
import time
from pathlib import Path

from .links import OnMessage, parse_lines

log = logging.getLogger("claude_display.wifi")

HOSTNAME = "claude-display.local"
PORT = 47322
TOKEN_FILE = Path(__file__).resolve().parents[2] / "secrets" / "wifi_token"
RESOLVE_EVERY_S = 60.0
SILENCE_RERESOLVE_S = 10.0


class UdpLink:
    name = "wifi"

    def __init__(self, token: str, on_message: OnMessage, host: str = HOSTNAME, port: int = PORT):
        self._prefix = b'{"k":"' + token.encode() + b'",'
        self._on_message = on_message
        self._host, self._port = host, port
        self._addr: tuple[str, int] | None = None
        self._resolved_at = 0.0
        self.last_rx = 0.0
        self.blocked = False  # macOS Local Network privacy refused the send
        self.probe_ok = False  # a ping over WiFi got a pong back
        self._probed_addr: tuple[str, int] | None = None
        self._probe_at = 0.0
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.settimeout(0.5)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="udp-link", daemon=True)

    @property
    def connected(self) -> bool:
        return self._addr is not None and not self.blocked

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def send(self, data: bytes) -> bool:
        addr = self._addr
        if addr is None or not data.startswith(b"{"):
            return False
        if self.blocked and not data.startswith(b'{"t":"cmd","c":"ping"'):
            return False
        try:
            self._sock.sendto(self._prefix + data[1:], addr)
            if self.blocked:
                log.info("wifi send works again")
            self.blocked = False
            return True
        except OSError as e:
            if e.errno in (errno.EHOSTUNREACH, errno.EPERM, errno.EACCES) and not self.blocked:
                log.warning("wifi send refused (%s): macOS Local Network permission for this Python?", e)
                self.blocked = True
            return False

    def _resolve(self) -> None:
        try:
            infos = socket.getaddrinfo(self._host, self._port, socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            if self._addr is not None:
                log.info("lost %s", self._host)
            self._addr = None
            return
        addr = infos[0][4][:2]
        if addr != self._addr:
            log.info("wifi display at %s:%d", *addr)
        self._addr = addr
        self._resolved_at = time.monotonic()

    def _run(self) -> None:
        buf = b""
        while not self._stop.is_set():
            now = time.monotonic()
            silent = now - max(self.last_rx, self._resolved_at)
            if self._addr is None or now - self._resolved_at > RESOLVE_EVERY_S or silent > SILENCE_RERESOLVE_S:
                self._resolve()
                if self._addr is None:
                    self._stop.wait(2.0)
                    continue
            if self._addr != self._probed_addr or (self.blocked and now - self._probe_at > 30):
                # Prove the path end to end even while USB carries the traffic; this is also
                # what surfaces a macOS Local Network permission problem at startup, and
                # notices when the user allows it later.
                self._probed_addr = self._addr
                self._probe_at = now
                self.send(b'{"t":"cmd","c":"ping"}\n')
            try:
                data, src = self._sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            if self._addr is None or src[0] != self._addr[0]:
                continue  # only the display may talk to us (a LAN host could fake BOOT presses)
            self.last_rx = time.monotonic()
            msgs, buf = parse_lines(buf + data + b"\n")
            for m in msgs:
                if m.get("t") == "pong":
                    if not self.probe_ok:
                        log.info("wifi path verified (pong)")
                    self.probe_ok = True
                    continue
                self._on_message(m, self.name)

    def summary(self) -> dict:
        return {"addr": self._addr[0] if self._addr else None, "blocked": self.blocked, "verified": self.probe_ok,
                "last_rx_s": round(time.monotonic() - self.last_rx, 1) if self.last_rx else None}


def make_udp_link(bridge, force: bool = False) -> UdpLink | None:
    if not TOKEN_FILE.exists():
        log.info("no %s: WiFi link disabled (run tools/provision_wifi.py)", TOKEN_FILE)
        return None
    return UdpLink(TOKEN_FILE.read_text().strip(), bridge.on_device_message)
