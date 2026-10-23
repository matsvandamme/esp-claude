import asyncio
import json
import time

import pytest

from claude_display.server import Bridge


class FakeLink:
    name = "fake"
    connected = True

    def __init__(self):
        self.sent = []

    def start(self):
        pass

    def send(self, data: bytes) -> bool:
        self.sent.append(json.loads(data))
        return True


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def http(port, method, path, body=None):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    data = json.dumps(body).encode() if body is not None else b""
    w.write(f"{method} {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
    await w.drain()
    resp = await r.read()
    w.close()
    head, _, payload = resp.partition(b"\r\n\r\n")
    return int(head.split()[1]), payload


@pytest.fixture
def bridge():
    clock = Clock()
    b = Bridge(clock=clock)
    link = FakeLink()
    b.add_link(link)
    return b, link, clock


def test_snapshot_pacing_and_heartbeat(bridge):
    b, link, clock = bridge
    b.maybe_send()
    assert link.sent[-1]["t"] == "snap" and link.sent[-1]["seq"] == 1
    clock.t += 0.3
    b.maybe_send()
    assert len(link.sent) == 1, "nothing changed, heartbeat not yet due"
    clock.t += 1.0
    b.maybe_send()
    hb = link.sent[-1]
    assert hb["t"] == "hb" and set(hb) == {"t", "tm", "tz"}, "the heartbeat carries the wall clock (D46)"
    assert abs(hb["tm"] - time.time()) < 5
    b.handle_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": "/p/x"})
    b.maybe_send()
    assert link.sent[-1]["t"] == "snap" and link.sent[-1]["f"]["st"] == "think"
    b.handle_hook({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash", "tool_use_id": "t"})
    b.maybe_send()
    assert link.sent[-1]["f"]["st"] == "think", "rate-limited within 50 ms"
    clock.t += 0.06
    b.maybe_send()
    assert link.sent[-1]["f"]["st"] == "tool"
    clock.t += 5.1
    b.maybe_send()
    assert link.sent[-1]["t"] == "snap", "periodic full resend"


def test_ack_latency_and_button(bridge):
    b, link, clock = bridge
    b.maybe_send()
    clock.t += 0.02
    b.on_device_message({"t": "ack", "seq": 1}, "fake")
    assert round(b.latency_ms[0]) == 20
    for sid in ("a", "b"):
        b.handle_hook({"hook_event_name": "SessionStart", "session_id": sid, "cwd": "/p/" + sid})
        clock.t += 1
    first = b.model.focus(clock.t).sid
    b.on_device_message({"t": "btn", "e": "short"}, "fake")
    assert b.model.focus(clock.t).sid != first


def test_button_press_counts_once_across_links(bridge):
    b, link, clock = bridge
    for sid in ("a", "b"):
        b.handle_hook({"hook_event_name": "SessionStart", "session_id": sid, "cwd": "/p/" + sid})
        clock.t += 1
    first = b.model.focus(clock.t).sid
    b.on_device_message({"t": "btn", "e": "short"}, "usb")
    clock.t += 0.02
    b.on_device_message({"t": "btn", "e": "short"}, "wifi")
    second = b.model.focus(clock.t).sid
    assert second != first, "one press over USB and Wi-Fi moves one step, not two"
    clock.t += 0.3
    b.on_device_message({"t": "btn", "e": "short"}, "usb")
    b.on_device_message({"t": "btn", "e": "short"}, "wifi")
    assert b.model.focus(clock.t).sid == first, "a quick second press still counts"


def test_http_endpoints(bridge):
    b, link, clock = bridge

    async def run():
        server = await asyncio.start_server(b._handle_http, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        st, out = await http(port, "POST", "/hook", {"hook_event_name": "UserPromptSubmit", "session_id": "s",
                                                     "cwd": "/p/proj", "prompt": "secret"})
        assert st == 200 and out == b"{}"
        st, out = await http(port, "POST", "/status", {"session_id": "s", "cost": {"total_cost_usd": 1.5},
                                                       "context_window": {"used_percentage": 40}})
        assert st == 200 and b"ctx 40%" in out and b"$" not in out  # D33
        st, out = await http(port, "GET", "/state")
        state = json.loads(out)
        assert state["sessions"][0]["project"] == "proj" and "secret" not in out.decode()
        st, _ = await http(port, "POST", "/hook", [1, 2])
        assert st == 400, "non-object JSON is rejected"
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"POST /hook HTTP/1.1\r\nContent-Length: 5\r\n\r\nnope!")
        await w.drain()
        assert b"400" in (await r.read()).split(b"\r\n")[0]
        st, _ = await http(port, "GET", "/nope")
        assert st == 404
        server.close()

    asyncio.run(run())


def test_falls_back_to_wifi_when_usb_is_gone():
    b = Bridge(clock=Clock())
    usb, wifi = FakeLink(), FakeLink()
    usb.name, wifi.name = "usb", "wifi"
    b.add_link(usb)
    b.add_link(wifi)
    b.maybe_send()
    assert b.active_link == "usb" and usb.sent and not wifi.sent
    usb.connected = False
    b.clock.t += 5.1
    b.maybe_send()
    assert b.active_link == "wifi" and wifi.sent
    usb.connected = True
    b.clock.t += 5.1
    b.maybe_send()
    assert b.active_link == "usb"


def test_udp_link_injects_token():
    from claude_display.wifi import UdpLink

    link = UdpLink("abc123", lambda m, n: None)
    sent = []
    link._addr = ("127.0.0.1", 9)
    link._sock = type("S", (), {"sendto": lambda self, d, a: sent.append(d)})()
    assert link.send(b'{"t":"hb"}\n')
    assert sent[0] == b'{"k":"abc123","t":"hb"}\n'
