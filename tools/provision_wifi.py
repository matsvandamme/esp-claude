#!/usr/bin/env python3
"""Provision the display's WiFi credentials (decisions D4, D16).

  read     read the SSID's password from the macOS System keychain (macOS asks for your
           login password once), create the UDP token, write secrets/creds.csv
  build    secrets/creds.csv -> secrets/creds.bin (NVS image for the `creds` partition)
  flash    write secrets/creds.bin to the display (port resolved by USB serial number)

Nothing secret is printed: only lengths and paths.
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = ROOT / "secrets"
CSV = SECRETS / "creds.csv"
BIN = SECRETS / "creds.bin"
TOKEN = SECRETS / "wifi_token"
CREDS_OFFSET = 0x10000
CREDS_SIZE = 0x3000
sys.path.insert(0, str(ROOT / "bridge"))


def write_private(path: Path, data: str) -> None:
    SECRETS.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(data)


def csv_escape(v: str) -> str:
    return '"' + v.replace('"', '""') + '"' if any(c in v for c in ',"\n') else v


def cmd_read(ssid: str) -> None:
    r = subprocess.run(
        ["security", "find-generic-password", "-D", "AirPort network password", "-a", ssid, "-w"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"keychain read failed (exit {r.returncode}); nothing written")
    password = r.stdout.rstrip("\n")
    token = TOKEN.read_text().strip() if TOKEN.exists() else secrets.token_hex(16)
    write_private(TOKEN, token + "\n")
    write_private(CSV, "key,type,encoding,value\nwifi,namespace,,\n"
                       f"ssid,data,string,{csv_escape(ssid)}\npass,data,string,{csv_escape(password)}\n"
                       f"token,data,string,{token}\n")
    print(f"wrote {CSV} (ssid {ssid!r}, password {len(password)} chars) and {TOKEN}")


def idf_python() -> Path:
    """The ESP-IDF Python env (set by export.sh; else the newest idf5.5 env under ~/.espressif)."""
    env = os.environ.get("IDF_PYTHON_ENV_PATH")
    if env:
        return Path(env) / "bin" / "python"
    found = sorted((Path.home() / ".espressif" / "python_env").glob("idf5.5_py*_env"),
                   key=lambda p: [int(n) for n in re.findall(r"\d+", p.name.split("_py")[1])])
    if not found:
        raise SystemExit("ESP-IDF 5.5 Python env not found: install ESP-IDF or set IDF_PYTHON_ENV_PATH")
    return found[-1] / "bin" / "python"


def cmd_build() -> None:
    gen = Path(os.environ.get("IDF_PATH", Path.home() / "esp" / "esp-idf")) / \
        "components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py"
    py = idf_python()
    subprocess.run([str(py), str(gen), "generate", str(CSV), str(BIN), hex(CREDS_SIZE)], check=True,
                   stdout=subprocess.DEVNULL)
    os.chmod(BIN, 0o600)
    print(f"built {BIN} ({BIN.stat().st_size} bytes)")


def cmd_flash() -> None:
    from claude_display.device import find_port

    port = find_port()
    if not port:
        raise SystemExit("display not connected")
    subprocess.run([str(idf_python()), "-m", "esptool", "--chip", "esp32c6", "--port", port, "write_flash", hex(CREDS_OFFSET), str(BIN)],
                   check=True, stdout=subprocess.DEVNULL)
    print(f"flashed creds partition at {hex(CREDS_OFFSET)} on {port}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["read", "build", "flash"])
    ap.add_argument("--ssid", help="your WiFi network name (needed for `read`)")
    a = ap.parse_args()
    if a.step == "read" and not a.ssid:
        ap.error("read needs --ssid <your network>")
    {"read": lambda: cmd_read(a.ssid), "build": cmd_build, "flash": cmd_flash}[a.step]()
    return 0


if __name__ == "__main__":
    sys.exit(main())
