"""The bridge daemon: hook/status ingest on 127.0.0.1, state model, snapshot pacing, links.

Claude Code side (decisions D9, D10):
  POST /hook    body = raw hook JSON from hooks/emit.sh          -> 200 "{}"
  POST /status  body = status-line JSON from hooks/statusline.sh -> 200 text for the status line
  GET  /state   debugging summary (no payload content)
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal
import statistics
import threading
import time
from collections import deque
from typing import Callable

from .model import NEEDS_USER, WORKING, Model
from .protocol import build_snapshot, clock_fields, encode, snapshot_key
from .transcript import SESSION_COLORS, TranscriptTail

log = logging.getLogger("claude_display")

HOST = "127.0.0.1"
PORT = 47321
MIN_SNAP_INTERVAL = 0.05   # at most 20 snapshots/s
HEARTBEAT_S = 1.0
RESEND_S = 5.0             # full snapshot periodically so a rebooted display catches up
TRANSCRIPT_POLL_S = 0.5
SAVE_EVERY_S = 5.0         # state file write debounce (D36)
MAX_BODY = 4 * 1024 * 1024
BTN_DUP_S = 0.5            # the display sends each BOOT press on every link it has (USB and Wi-Fi)


class Bridge:
    def __init__(self, model: Model | None = None, clock: Callable[[], float] = time.monotonic, store=None):
        self.model = model or Model()
        self.clock = clock
        self.store = store  # persist.Store, optional
        self._last_save = 0.0
        if store:
            store.load(self.model, clock())
        self.links: list = []
        self.tail = TranscriptTail()
        self.seq = 0
        self._last_key = ""
        self._last_snap_t = 0.0
        self._last_send_t = 0.0
        self._sent_at: dict[int, float] = {}
        self.latency_ms: deque[float] = deque(maxlen=5000)
        self.device_stats: dict = {}
        self.active_link = "none"
        self._shot = None  # (ShotAssembler, Future) while a screenshot is in flight
        self._last_btn: tuple[str, float] | None = None  # (link, time) of the last press handled
        self.watcher = None  # GhosttyWatcher, optional
        self.ota_events = None  # asyncio.Queue while a firmware update runs
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self.started = time.time()

    # ---------- links ----------

    def add_link(self, link) -> None:
        self.links.append(link)

    def on_link_connect(self, name: str) -> None:
        # From a link thread: force a full snapshot soon.
        self._last_key = ""
        if self._loop:
            self._loop.call_soon_threadsafe(self._wake.set)

    def on_device_message(self, msg: dict, link: str) -> None:
        if self._loop and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(self._handle_device_message, msg, link)
        else:
            self._handle_device_message(msg, link)

    def _handle_device_message(self, msg: dict, link: str) -> None:
        t = msg.get("t")
        if t == "ack":
            sent = self._sent_at.pop(int(msg.get("seq", -1)), None)
            if sent is not None:
                self.latency_ms.append((self.clock() - sent) * 1000)
        elif t == "btn":
            now = self.clock()
            last = self._last_btn
            if last and last[0] != link and now - last[1] < BTN_DUP_S:
                return  # the same press arriving over the other link
            self._last_btn = (link, now)
            self.model.on_button(now)
            self._wake.set()
        elif t == "stats":
            self.device_stats = msg
        elif t == "ota" and self.ota_events is not None:
            self.ota_events.put_nowait(msg)
        elif t == "shot" and self._shot:
            self._shot[0].add(msg)
        elif t == "shot_end" and self._shot and not self._shot[1].done():
            self._shot[1].set_result(self._shot[0].png())

    def _send(self, msg: dict, usb_only: bool = False) -> str:
        data = encode(msg)
        for link in self.links:
            if usb_only and link.name != "usb":
                continue
            if link.connected and link.send(data):
                return link.name
        return "none"

    def on_window(self, w) -> None:
        """From the Ghostty watcher thread."""
        def apply() -> None:
            self.model.set_window(w.frontmost, w.cwd, self.clock(), w.terminal_id)
            self._wake.set()

        if self._loop:
            self._loop.call_soon_threadsafe(apply)

    # ---------- ingest ----------

    def handle_hook(self, payload: dict) -> None:
        ev = payload.get("hook_event_name", "?")
        t0 = time.perf_counter()
        self.model.on_hook(payload, self.clock())
        log.debug("hook %s %.2fms", ev, (time.perf_counter() - t0) * 1000)
        self._wake.set()

    def handle_status(self, payload: dict) -> str:
        self.model.on_status(payload, self.clock())
        self._wake.set()
        return self.status_line(str(payload.get("session_id", "")))

    def status_line(self, sid: str) -> str:
        s = self.model.sessions.get(sid)
        parts = []
        if s and s.ctx_pct is not None:
            parts.append(f"ctx {s.ctx_pct}%")
        rates = self.model.rates(time.time())
        if "h5" in rates:
            parts.append(f"5h {rates['h5']}%")
        if "d7" in rates:
            parts.append(f"7d {rates['d7']}%")
        dot = "\x1b[38;2;215;119;87m✻\x1b[0m" if self.active_link != "none" else "\x1b[2m✻\x1b[0m"
        link = self.active_link if self.active_link != "none" else "display offline"
        return f"{dot} \x1b[2m{' · '.join(parts + [link])}\x1b[0m"

    def state_summary(self) -> dict:
        now = self.clock()
        lat = sorted(self.latency_ms)

        def pct(p: float) -> float | None:
            return round(lat[min(len(lat) - 1, int(p * len(lat)))], 1) if lat else None

        focus = self.model.focus(now)
        return {
            "link": self.active_link,
            "sessions": [
                {"id": s.sid[:8], "project": s.project, "name": s.name or None, "state": s.state, "tools": s.ntools,
                 "subagents": len(s.subagents), "model": s.model, "ctx_pct": s.ctx_pct, "cost": s.cost}
                for s in self.model.ordered()
            ],
            "focus": focus.sid[:8] if focus else None,
            "rates": self.model.rates(time.time()),
            "seq": self.seq,
            "ack_ms": {"n": len(lat), "p50": pct(0.5), "p95": pct(0.95), "max": round(lat[-1], 1) if lat else None,
                       "mean": round(statistics.fmean(lat), 1) if lat else None},
            "device": self.device_stats,
            "wifi": next((l.summary() for l in self.links if l.name == "wifi"), None),
            "window": {**self.watcher.summary(), "session": (self.model.window_sid or "")[:8] or None}
            if self.watcher else None,
            "uptime_s": int(time.time() - self.started),
        }

    # ---------- loops ----------

    async def screenshot(self) -> bytes | None:
        """Capture the display through whichever link is up (USB only: rows are large)."""
        from .devctl import ShotAssembler

        if self._shot or not any(l.name == "usb" and l.connected for l in self.links):
            return None
        fut = asyncio.get_running_loop().create_future()
        self._shot = (ShotAssembler(), fut)
        try:
            self._send({"t": "cmd", "c": "shot"}, usb_only=True)
            return await asyncio.wait_for(fut, timeout=10)
        except asyncio.TimeoutError:
            return None
        finally:
            self._shot = None

    async def firmware_update(self, body: bytes) -> dict:
        from .ota import OtaError, push

        if self.ota_events is not None:
            return {"ok": False, "error": "an update is already running"}
        try:
            req = json.loads(body or b"{}")
            return await push(self, str(req["path"]), str(req.get("via", "wifi")))
        except (OtaError, OSError, KeyError, ValueError) as e:
            return {"ok": False, "error": str(e)}

    async def device_stats_now(self) -> dict:
        self._send({"t": "cmd", "c": "stats"})
        await asyncio.sleep(0.3)
        return self.device_stats

    def maybe_send(self) -> None:
        now = self.clock()
        snap = build_snapshot(self.model, now)
        if any(getattr(link, "blocked", False) for link in self.links):
            snap["wb"] = 1  # D22: show "wifi blocked" on the display
        key = snapshot_key(snap)
        due_resend = now - self._last_snap_t >= RESEND_S
        if (key != self._last_key or due_resend) and now - self._last_snap_t >= MIN_SNAP_INTERVAL:
            self.seq += 1
            snap["seq"] = self.seq
            self._sent_at[self.seq] = now
            if len(self._sent_at) > 200:
                for k in sorted(self._sent_at)[:100]:
                    del self._sent_at[k]
            self.active_link = self._send(snap)
            self._last_key = key
            self._last_snap_t = self._last_send_t = now
        elif now - self._last_send_t >= HEARTBEAT_S:
            self.active_link = self._send({"t": "hb", **clock_fields(self.model.wall())})
            self._last_send_t = now

    def poll_transcripts(self) -> None:
        now = self.clock()
        for s in list(self.model.sessions.values()):
            if not s.transcript_path:
                continue
            for kind in self.tail.poll(s.transcript_path):
                if kind == "activity":
                    self.model.on_transcript_activity(s.sid, now)
                elif kind.startswith("usage:"):
                    self.model.on_transcript_activity(s.sid, now)
                    msg_id, _, n = kind[6:].rpartition(":")
                    self.model.on_transcript_usage(msg_id, int(n))
                elif kind.startswith("color:"):
                    self.model.on_transcript_color(s.sid, SESSION_COLORS.get(kind[6:], -1))
                    self._wake.set()
                elif s.state in WORKING | NEEDS_USER:
                    log.info("transcript %s -> idle (%s)", s.sid[:8], kind)
                    self.model.on_transcript_interrupt(s.sid, now, denied=kind == "denied")
                    self._wake.set()

    async def _sender(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.25)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            self.maybe_send()
            # Respect the snapshot rate limit without dropping the last change.
            await asyncio.sleep(MIN_SNAP_INTERVAL)
            self.maybe_send()

    async def _ticker(self) -> None:
        while True:
            await asyncio.sleep(TRANSCRIPT_POLL_S)
            self.model.tick(self.clock())
            self.poll_transcripts()
            self.save_state()

    def save_state(self, force: bool = False) -> None:
        now = self.clock()
        if not self.store or self.model.version == self.store.saved_version:
            return
        if force or now - self._last_save >= SAVE_EVERY_S:
            self.store.save(self.model, now)
            self._last_save = now

    # ---------- HTTP ----------

    async def _handle_http(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2)
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = (lines[0].split(" ", 2) + ["", ""])[:3]
            headers = {k.strip().lower(): v.strip() for k, v in (h.split(":", 1) for h in lines[1:] if ":" in h)}
            n = min(int(headers.get("content-length", "0") or 0), MAX_BODY)
            body = await asyncio.wait_for(reader.readexactly(n), timeout=2) if n else b""
            status, ctype, out = 200, "application/json", b"{}"
            if method == "POST" and path in ("/hook", "/status"):
                try:
                    payload = json.loads(body or b"{}")
                except ValueError:
                    payload = None
                if not isinstance(payload, dict):
                    status, out = 400, b'{"error":"bad json"}'
                elif path == "/hook":
                    self.handle_hook(payload)
                else:
                    ctype, out = "text/plain; charset=utf-8", self.handle_status(payload).encode()
            elif method == "GET" and path == "/state":
                out = json.dumps(self.state_summary()).encode()
            elif method == "GET" and path == "/stats":
                out = json.dumps(await self.device_stats_now()).encode()
            elif method == "POST" and path == "/ota":
                out = json.dumps(await self.firmware_update(body)).encode()
            elif method == "GET" and path == "/shot":
                png = await self.screenshot()
                if png is None:
                    status, out = 404, b'{"error":"no display"}'
                else:
                    ctype, out = "image/png", png
            else:
                status, out = 404, b'{"error":"not found"}'
            reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}[status]
            writer.write(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(out)}\r\n"
                f"Connection: close\r\n\r\n".encode() + out
            )
            await writer.drain()
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError, ValueError):
            pass
        finally:
            writer.close()

    async def serve(self, host: str = HOST, port: int = PORT) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        for link in self.links:
            link.start()
        if self.watcher:
            self.watcher.start()
        server = await asyncio.start_server(self._handle_http, host, port)
        log.info("listening on http://%s:%d", host, port)
        main = asyncio.current_task()
        for sig in (signal.SIGTERM, signal.SIGINT):
            # launchd stops the agent with SIGTERM: flush state first (D36).
            self._loop.add_signal_handler(sig, lambda: main and main.cancel())
        try:
            async with server:
                await asyncio.gather(server.serve_forever(), self._sender(), self._ticker())
        except asyncio.CancelledError:
            pass
        finally:
            self.save_state(force=True)

