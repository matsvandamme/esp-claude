"""Locate and open the esp-claude display over USB-Serial/JTAG.

The board is identified by its USB serial number (the chip's base MAC), never by
port name: another Espressif board on this Mac must never be touched.

The serial comes from $CLAUDE_DISPLAY_SERIAL, else secrets/device_serial. With neither
set, the only attached Espressif board is used; with several attached, none is.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import serial
from serial.tools import list_ports

SERIAL_FILE = Path(__file__).resolve().parents[2] / "secrets" / "device_serial"
ESPRESSIF_VID = 0x303A


def configured_serial() -> str | None:
    s = os.environ.get("CLAUDE_DISPLAY_SERIAL", "").strip()
    if not s:
        try:
            s = SERIAL_FILE.read_text().strip()
        except OSError:
            return None
    return s.upper() or None


def espressif_ports() -> list:
    return [p for p in list_ports.comports() if p.vid == ESPRESSIF_VID]


def _cu(p) -> str:
    return p.device.replace("/dev/tty.", "/dev/cu.")


def find_port(usb_serial: str | None = None) -> str | None:
    """The display's port, or None when it is absent or ambiguous (never raises)."""
    usb_serial = (usb_serial or configured_serial() or "").upper()
    ports = espressif_ports()
    if usb_serial:
        for p in ports:
            if (p.serial_number or "").upper() == usb_serial:
                return _cu(p)
        return None
    return _cu(ports[0]) if len(ports) == 1 else None


def open_port(port: str | None = None, timeout: float = 0.05) -> serial.Serial:
    """Open without resetting the chip.

    USB-Serial/JTAG resets the chip when RTS is asserted while DTR is not. pyserial applies
    DTR before RTS after the OS opens the port with both asserted, so the obvious
    dtr=False/rts=False passes through the reset state. Holding DTR high until RTS has
    dropped avoids it (verified: uptime keeps counting across opens).
    """
    port = port or find_port()
    if not port:
        raise FileNotFoundError(f"display ({configured_serial() or 'auto-detect'}) not connected")
    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = timeout
    s.exclusive = True  # one owner: a second reader would steal the other's bytes
    s.dtr = True
    s.rts = False
    s.open()
    s.dtr = False
    return s


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m claude_display.device",
                                 description="Print the display's port (empty when not found).")
    ap.add_argument("--list", action="store_true", help="list attached Espressif boards")
    ap.add_argument("--save", metavar="SERIAL", help=f"remember this board's USB serial in {SERIAL_FILE}")
    a = ap.parse_args()
    if a.list:
        want = configured_serial()
        for p in espressif_ports():
            mark = "  <- configured" if want and (p.serial_number or "").upper() == want else ""
            print(f"{_cu(p)}  serial {p.serial_number}{mark}")
        return 0
    if a.save:
        SERIAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        SERIAL_FILE.write_text(a.save.strip().upper() + "\n")
        print(f"saved {a.save.strip().upper()} to {SERIAL_FILE}")
        return 0
    port = find_port()
    print(port or "")
    if not port and not configured_serial() and len(espressif_ports()) > 1:
        print("several Espressif boards attached: pick the display with --list, then --save SERIAL",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
