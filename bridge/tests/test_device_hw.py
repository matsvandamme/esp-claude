"""Hardware tests: run only when the display is plugged in (skipped otherwise)."""

import pytest

from claude_display.device import find_port
from claude_display.devctl import DevLink

pytestmark = pytest.mark.skipif(find_port() is None, reason="display not connected")


@pytest.fixture(autouse=True)
def _port_free():
    try:
        DevLink().close()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"display port busy (bridge daemon running?): {e}")


def _uptime_ms(d: DevLink) -> int:
    d.cmd("ping")
    m = d.wait_for("pong", 1.5)
    assert m, "no pong"
    return m["up"]


def test_port_open_close_does_not_reset():
    d = DevLink()
    last = _uptime_ms(d)
    d.close()
    for _ in range(100):
        d = DevLink()
        d.close()
    d = DevLink()
    now = _uptime_ms(d)
    d.close()
    assert now > last, f"chip reset during open/close cycles (uptime {last} -> {now} ms)"
