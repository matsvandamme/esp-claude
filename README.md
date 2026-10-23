# esp-claude

A Claude Code status display on a Waveshare **ESP32-C6-LCD-1.47** (ESP32-C6, ST7789 1.47″ 172×320).
It mirrors every Claude Code session on your Mac in real time:

- **The session in your focused Ghostty window,** titled with the name you gave it via `/name` (otherwise its folder) and coloured with its `/color`. Switch tabs or windows and the display follows. In another app it keeps the last focused session.
- **What that session is doing.** Thinking (the `✻` spinner and a whimsical verb), running a tool (`Bash · npm test`, with commands masked and truncated), compacting, done, or an API error.
- **Clawd**, the Claude Code mascot, animated per state: blinking, looking around, typing, waving when it needs you, happy when done, X-eyes on error, sleeping when nothing is running.
- **"Needs you" alerts.** Permission prompts, questions, plan approval and MCP input show an orange banner, a waving Clawd and a pulsing LED. After 60 s unanswered the banner flashes and the LED blinks faster.
- **Usage.** Three gauges along the bottom: plan usage left in the current 5-hour session and the week (each with a reset countdown), and how full the session's context is. When a plan limit is hit the screen says when it lifts ("Back in 1h12m").
- **Other sessions.** When a session you're not looking at needs you, a `⚠ project` badge appears in the header and the LED pulses.
- **BOOT button.**
  - A short press shows the next session, along with a list of all your sessions (state and how long) for 3 s.
  - A long press switches between high and low brightness. The brightness never changes by itself, and your choice survives a reboot.
  - Selecting a Ghostty window shows its session too; whichever you did last stays on screen.
- **Done while away.** A turn of a minute or more that finishes while you're in another terminal or app shows a green "Done while away" banner, or a `✓ project` badge for another session, and a slow green LED. These stay until you select that terminal.
- **Warnings.**
  - A tool running for more than 3 minutes shows an amber "still running".
  - From 80 % context the ctx gauge turns amber and says `→ compact` (red from 95 %).
  - When the 5-hour or weekly allowance drops under 15 %, the LED flashes amber three times.
- **Clock and today.** The time is always in the header. When nothing is working for 2 minutes, a big clock and the date take over, along with today's turns, active time and new tokens (input + output + newly cached).

Only the tool name and a short masked target leave Claude Code. Prompts and replies are never sent or logged.

## How it works

```
Claude Code ──hooks (hooks/emit.sh)──────► bridge daemon (LaunchAgent, 127.0.0.1:47321) ──USB serial──► display
            ──status line (hooks/statusline.sh)─┘   session model, transcript tailing       └─UDP/WiFi (token)─┘
```

- `bridge/`: Python 3.13 daemon (uv project). It reads hook events and status-line JSON, keeps per-session state, and sends at most 20 snapshots a second plus a 1 Hz heartbeat.
  USB is preferred. WiFi (`claude-display.local:47322`) takes over when the board is not plugged into this Mac.
- `firmware/`: ESP-IDF 5.5 + LVGL 9, SPI at 80 MHz with DMA. Measured 48–64 fps while animating, with every frame under 25 ms.
- `enclosure/`: a printable three-part desk stand (OpenSCAD). See [enclosure/README.md](enclosure/README.md).

Code comments cite design decisions as `D1`–`D52`. They refer to the project's internal design log, which isn't published.

## Requirements

- **macOS.** The bridge runs as a LaunchAgent, the WiFi password comes from the macOS keychain, and window-following uses Ghostty's AppleScript.
- **[Ghostty](https://ghostty.org)** for the display to follow the focused window. In other terminals everything else still works.
- **A Waveshare [ESP32-C6-LCD-1.47](https://www.waveshare.com/wiki/ESP32-C6-LCD-1.47)**.
- **ESP-IDF 5.5** at `~/esp/esp-idf`, or wherever `$IDF_PATH` points (`./install.sh esp32c6`).
- **[uv](https://docs.astral.sh/uv/).** Node is needed only to regenerate the fonts.
- **A 2.4 GHz WiFi network** for the optional wireless link. The ESP32-C6 has no 5 GHz.

## Install

```sh
cd bridge && uv sync
uv run python -m claude_display.device --list             # find your board
uv run python -m claude_display.device --save <SERIAL>    # only if several Espressif boards are attached
cd ..
tools/flash.sh erase-flash                  # first time; afterwards plain tools/flash.sh
uv run --project bridge python tools/provision_wifi.py read --ssid "<your network>"   # macOS asks for your login password
uv run --project bridge python tools/provision_wifi.py build
uv run --project bridge python tools/provision_wifi.py flash
python3 tools/install.py                    # hooks + status line + LaunchAgent
```

The tools find the display by its USB serial number, so they never touch another Espressif board. With only one attached, it is picked automatically. Otherwise the serial comes from `--save`, which writes the gitignored `secrets/device_serial`, or from `$CLAUDE_DISPLAY_SERIAL`.

`install.py` backs up `~/.claude/settings.json`, then adds only the `hooks` and `statusLine` keys. Running sessions pick up the status line at once; hooks reach sessions started afterwards.

**One-time manual steps** (everything else is automated):
1. Approve the macOS keychain prompt when `provision_wifi.py read` reads the WiFi password.
2. Allow **python3.13** under System Settings → Privacy & Security → **Local Network**. Without it the WiFi fallback is blocked and the header shows `✗wifi`.
3. Click **OK** when macOS asks whether python3.13 may control **Ghostty**. That is how the display knows which window is focused; Ghostty's AppleScript only reads the focused terminal's folder and title.
4. After the first flash, glance at the panel orientation (upright with the USB-C connector on the left; flip `LCD_MIRROR_X`/`LCD_MIRROR_Y` in `firmware/main/board.h` to turn it around).

## Uninstall

```sh
python3 tools/uninstall.py --dry-run   # shows the result and compares it with the pre-install backup
python3 tools/uninstall.py             # removes our hooks + statusLine and the LaunchAgent
```

## Tools

| Command | What it does |
|---|---|
| `curl -s 127.0.0.1:47321/state` | sessions, focus, link, ack latency, device stats, WiFi status |
| `curl -s 127.0.0.1:47321/stats` | device: fps, frame times, heap, backlight, LED mode, WiFi, UDP drops |
| `curl -s -o shot.png 127.0.0.1:47321/shot` | screenshot of the display (needs USB) |
| `uv run --project bridge python -m claude_display demo --minutes 5` | fake sessions through the real pipeline |
| `uv run --project bridge python tools/screenshot.py --all-states` | every UI state from `assets/snapshots/` (agent must be paused) |
| `uv run --project bridge python tools/e2e_interactive.py` | real `claude --model haiku` session incl. a permission prompt |
| `cd bridge && uv run pytest` | unit + protocol tests; the hardware test runs when the port is free |
| `tools/flash.sh` | build + flash over USB (pauses the LaunchAgent while flashing) |
| `tools/flash.sh --wifi` | build + update over WiFi through the running bridge; a bad image rolls back by itself |

Logs: `~/Library/Logs/claude-display.log` (event names and link changes only).
State: `~/Library/Application Support/claude-display/state.json` (sessions and plan limits, so a bridge restart doesn't blank the screen; no prompt text).

## Troubleshooting

- **`flash.sh` says "display not found"**: several Espressif boards are attached and none is chosen. Run `python -m claude_display.device --list`, then `--save` the display's serial.
- **Screen says "Bridge offline"**: the LaunchAgent isn't running. Check it with `launchctl print gui/$(id -u)/com.github.matsvandamme.esp-claude`, then restart it with `python3 tools/install.py`.
- **WiFi never connects** (`/stats` → `"wifi":"no_ap"`): the ESP32-C6 only supports **2.4 GHz** networks.
- **`✗wifi` in the header**: macOS Local Network permission for python3.13 is off (see step 2).
- **Display doesn't follow the window**: check `curl -s 127.0.0.1:47321/state` → `window.error`. If it is -1743, re-enable python3.13 → Ghostty under Privacy & Security → Automation.
- **After `erase-flash`** the WiFi credentials are gone. Re-run `provision_wifi.py build` and `flash`.

## Known limitations

- **Same folder, two sessions.** Sessions are matched to windows by folder, because Ghostty exposes no TTY. With two sessions in the same folder, the most recently active one wins.
- **No cost figure.** Claude Code's session cost is an API-equivalent price, not what a subscription pays, so it isn't shown.
- **Model-specific weekly caps.** The status line only reports the 5-hour and all-models weekly windows. If a model-specific cap is what stops you, the limit screen shows without a countdown.
- **Denied permissions and Esc.** No hook fires for either, so they are detected by tailing the session transcript. This takes up to 0.5 s.
- **Screenshots need USB.** They go over the serial link only.
- **macOS only.** Linux and Windows would need another service manager, keychain and window tracker.
- **Font regeneration** (`tools/gen_fonts.sh`) expects JetBrains Mono Nerd Font in `~/Library/Fonts` or at `$JB`. The generated fonts are committed, so this is only needed to change the glyph set.

## License and credits

The code and the enclosure are MIT-licensed: see [LICENSE](LICENSE).

- [LVGL](https://lvgl.io) (MIT) is fetched by the ESP-IDF component manager.
- The fonts are generated from [JetBrains Mono](https://www.jetbrains.com/lp/mono/) (SIL OFL 1.1, via [Nerd Fonts](https://www.nerdfonts.com)) and [DejaVu Sans Mono](https://dejavu-fonts.github.io) (Bitstream Vera license) with [lv_font_conv](https://github.com/lvgl/lv_font_conv).

**This is an unofficial fan project, not affiliated with or endorsed by Anthropic.** Claude, Claude Code and the Clawd mascot belong to Anthropic. The display's colours follow Claude Code's terminal theme.
