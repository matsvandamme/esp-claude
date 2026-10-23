"""Which board find_port() picks: configured serial first, auto-detect only when unambiguous."""

from types import SimpleNamespace

import pytest

from claude_display import device

DISPLAY = SimpleNamespace(vid=0x303A, serial_number="AA:BB:CC:DD:EE:01", device="/dev/tty.usbmodem1")
OTHER = SimpleNamespace(vid=0x303A, serial_number="AA:BB:CC:DD:EE:02", device="/dev/tty.usbmodem2")
NOT_ESP = SimpleNamespace(vid=0x1234, serial_number="X", device="/dev/tty.usbserial9")


@pytest.fixture
def ports(monkeypatch, tmp_path):
    monkeypatch.delenv("CLAUDE_DISPLAY_SERIAL", raising=False)
    monkeypatch.setattr(device, "SERIAL_FILE", tmp_path / "device_serial")
    attached = []
    monkeypatch.setattr(device.list_ports, "comports", lambda: attached)
    return attached


def test_single_board_is_auto_detected(ports):
    ports += [DISPLAY, NOT_ESP]
    assert device.find_port() == "/dev/cu.usbmodem1"


def test_several_boards_without_config_picks_none(ports):
    ports += [DISPLAY, OTHER]
    assert device.find_port() is None


def test_env_var_selects_board(ports, monkeypatch):
    ports += [DISPLAY, OTHER]
    monkeypatch.setenv("CLAUDE_DISPLAY_SERIAL", "aa:bb:cc:dd:ee:02")
    assert device.find_port() == "/dev/cu.usbmodem2"


def test_serial_file_selects_board(ports):
    ports += [DISPLAY, OTHER]
    device.SERIAL_FILE.write_text("AA:BB:CC:DD:EE:01\n")
    assert device.find_port() == "/dev/cu.usbmodem1"


def test_configured_board_absent_never_falls_back(ports):
    ports += [OTHER]
    device.SERIAL_FILE.write_text("AA:BB:CC:DD:EE:01\n")
    assert device.find_port() is None
