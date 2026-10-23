"""Transports to the display. Both carry the same newline-delimited JSON (decision D1)."""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable

import serial

from .device import find_port, open_port

log = logging.getLogger("claude_display.links")

OnMessage = Callable[[dict, str], None]


def parse_lines(buf: bytes) -> tuple[list[dict], bytes]:
    msgs = []
    *lines, rest = buf.split(b"\n")
    for raw in lines:
        raw = raw.strip()
        if raw.startswith(b"{"):
            try:
                msgs.append(json.loads(raw))
            except ValueError:
                pass
    return msgs, rest[-65536:]


class UsbLink:
    """Owns the display's USB-Serial/JTAG port; reconnects whenever the board reappears."""

    name = "usb"

    def __init__(self, on_message: OnMessage, on_connect: Callable[[str], None] | None = None):
        self._on_message = on_message
        self._on_connect = on_connect
        self._ser: serial.Serial | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="usb-link", daemon=True)

    @property
    def connected(self) -> bool:
        return self._ser is not None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self._close()

    def send(self, data: bytes) -> bool:
        with self._lock:
            ser = self._ser
            if ser is None:
                return False
            try:
                ser.write(data)
                return True
            except (serial.SerialException, OSError) as e:
                log.warning("usb write failed: %s", e)
                self._close_locked()
                return False

    def _close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001 - closing a dead port can raise anything
                pass
            self._ser = None

    def _run(self) -> None:
        buf = b""
        while not self._stop.is_set():
            if self._ser is None:
                port = find_port()
                if not port:
                    self._stop.wait(1.0)
                    continue
                try:
                    ser = open_port(port, timeout=0.05)
                except (serial.SerialException, OSError) as e:
                    log.warning("usb open %s failed: %s", port, e)
                    self._stop.wait(1.0)
                    continue
                with self._lock:
                    self._ser = ser
                buf = b""
                log.info("usb connected on %s", port)
                if self._on_connect:
                    self._on_connect(self.name)
            try:
                ser = self._ser
                # Block for the first byte only, then take whatever else is buffered:
                # read(4096) would sit out the whole timeout on every short message.
                chunk = ser.read(1) if ser else b""
                if chunk and ser.in_waiting:
                    chunk += ser.read(ser.in_waiting)
            except (serial.SerialException, OSError, TypeError, AttributeError) as e:
                log.info("usb disconnected: %s", e)
                self._close()
                continue
            if chunk:
                msgs, buf = parse_lines(buf + chunk)
                for m in msgs:
                    self._on_message(m, self.name)
