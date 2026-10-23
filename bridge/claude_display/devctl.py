"""Small synchronous helpers for talking to the display over USB (tests and tools)."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from PIL import Image

from .device import open_port

W, H = 320, 172


class ShotAssembler:
    """Collects {"t":"shot"} rows (little-endian RGB565, base64) into an image."""

    def __init__(self) -> None:
        self.img = Image.new("RGB", (W, H))
        self._px = self.img.load()
        self.rows = 0

    def add(self, m: dict) -> None:
        data = base64.b64decode(m["d"])
        x0, y = m["x"], m["y"]
        for i in range(len(data) // 2):
            v = data[2 * i] | (data[2 * i + 1] << 8)
            self._px[x0 + i, y] = (((v >> 11) & 0x1F) * 255 // 31, ((v >> 5) & 0x3F) * 255 // 63, (v & 0x1F) * 255 // 31)
        self.rows += 1

    def png(self) -> bytes:
        import io

        out = io.BytesIO()
        self.img.save(out, "PNG")
        return out.getvalue()


class DevLink:
    def __init__(self, port: str | None = None):
        self.ser = open_port(port)
        self._buf = b""

    def close(self) -> None:
        self.ser.close()

    def send(self, msg: dict) -> None:
        self.ser.write((json.dumps(msg, separators=(",", ":")) + "\n").encode())

    def lines(self, timeout: float):
        """Yield parsed JSON messages until `timeout` seconds pass without the caller stopping."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            chunk = self.ser.read(1)
            if chunk and self.ser.in_waiting:
                chunk += self.ser.read(self.ser.in_waiting)
            if chunk:
                self._buf += chunk
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                raw = raw.strip()
                if raw.startswith(b"{"):
                    try:
                        yield json.loads(raw)
                    except ValueError:
                        pass

    def wait_for(self, t: str, timeout: float = 2.0) -> dict | None:
        for m in self.lines(timeout):
            if m.get("t") == t:
                return m
        return None

    def cmd(self, c: str, **kw) -> None:
        self.send({"t": "cmd", "c": c, **kw})

    def stats(self) -> dict | None:
        self.cmd("stats")
        return self.wait_for("stats")

    def screenshot(self, path: str | Path, timeout: float = 10.0) -> Path:
        shot = ShotAssembler()
        self.cmd("shot")
        for m in self.lines(timeout):
            if m.get("t") == "shot":
                shot.add(m)
            elif m.get("t") == "shot_end":
                break
        else:
            raise TimeoutError(f"screenshot incomplete ({shot.rows} rows)")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        shot.img.save(path)
        return path
