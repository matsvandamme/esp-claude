"""claude-display command line.

  python -m claude_display serve [--no-usb] [--wifi auto|off|force] [-v]
  python -m claude_display state            # print the running daemon's summary
  python -m claude_display demo --minutes 5 # synthetic sessions against the running daemon
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import urllib.request


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="claude_display")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--no-usb", action="store_true")
    sv.add_argument("--wifi", choices=["auto", "off", "force"], default="auto")
    sv.add_argument("--port", type=int, default=47321)
    sv.add_argument("--no-window", action="store_true", help="don't follow the focused Ghostty window")
    sv.add_argument("--state-file", help="where sessions survive restarts (default: ~/Library/Application Support)")
    sv.add_argument("--no-state", action="store_true", help="don't save or restore sessions")
    sv.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("state")
    dm = sub.add_parser("demo")
    dm.add_argument("--minutes", type=float, default=2)
    dm.add_argument("--sessions", type=int, default=3)
    dm.add_argument("--seed", type=int)
    a = ap.parse_args(argv)

    if a.cmd == "state":
        with urllib.request.urlopen("http://127.0.0.1:47321/state", timeout=2) as r:
            print(json.dumps(json.load(r), indent=1))
        return 0
    if a.cmd == "demo":
        from .demo import run

        print(f"{run(a.minutes, a.sessions, a.seed)} turns")
        return 0

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from .links import UsbLink
    from .server import Bridge

    from .persist import DEFAULT_PATH, Store

    bridge = Bridge(store=None if a.no_state else Store(a.state_file or DEFAULT_PATH))
    if not a.no_usb and a.wifi != "force":
        bridge.add_link(UsbLink(bridge.on_device_message, bridge.on_link_connect))
    if a.wifi != "off":
        from .wifi import make_udp_link

        link = make_udp_link(bridge, force=a.wifi == "force")
        if link:
            bridge.add_link(link)
    if not a.no_window:
        from .frontmost import GhosttyWatcher

        bridge.watcher = GhosttyWatcher(bridge.on_window)
    try:
        asyncio.run(bridge.serve(port=a.port))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
