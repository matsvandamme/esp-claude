#!/usr/bin/env python3
"""Drive the display through every fixture in assets/snapshots and capture screenshots.

Usage: uv run --project bridge python tools/screenshot.py --all-states [--out artifacts/shots]
       uv run --project bridge python tools/screenshot.py --fixture 04_tool
       uv run --project bridge python tools/screenshot.py --offline
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bridge"))
from claude_display.devctl import DevLink  # noqa: E402

FIXTURES = ROOT / "assets" / "snapshots"


def show(d: DevLink, snap: dict, seq: int) -> dict | None:
    d.send({**snap, "seq": seq})
    return d.wait_for("ack", 2.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all-states", action="store_true")
    ap.add_argument("--fixture")
    ap.add_argument("--offline", action="store_true", help="capture the bridge-offline screen")
    ap.add_argument("--settle", type=float, default=0.8, help="seconds to let animations run")
    ap.add_argument("--out", default=str(ROOT / "artifacts" / "shots"))
    a = ap.parse_args()
    out = Path(a.out)
    d = DevLink()
    names = sorted(p.stem for p in FIXTURES.glob("*.json")) if a.all_states else ([a.fixture] if a.fixture else [])
    written = []
    if a.offline or a.all_states:
        time.sleep(5.5)  # no messages for >5 s -> offline screen
        written.append(d.screenshot(out / "00_offline.png"))
    for i, name in enumerate(names, 1):
        snap = json.loads((FIXTURES / f"{name}.json").read_text())
        ack = show(d, snap, i)
        if not ack:
            print(f"{name}: no ack", file=sys.stderr)
            return 1
        time.sleep(a.settle)
        d.send({"t": "hb"})
        written.append(d.screenshot(out / f"{name}.png"))
    d.close()
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
