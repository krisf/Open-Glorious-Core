# Open Glorious Core

Open Glorious Core is an open-source Linux desktop app for Glorious peripherals.

Right now the project is focused on RGB control and has only been tested properly with the GMMK Pro, but the long-term goal is broader support for more Glorious keyboards, mice, and other peripherals.

## Current status

What works today:

- device detection on Linux through `hidraw`
- stock-firmware RGB control for the GMMK Pro
- solid colors
- breathing
- wave effects
- brightness control
- speed control
- custom colors with picker, RGB, and hex input
- native desktop window with an editable HTML/CSS/JS frontend

What is planned later:

- keybind and shortcut editing
- broader Glorious device support
- deeper effect mapping and firmware behavior cleanup

## Install

There are a few ways to install it depending on how you want to use it.

### Local user install

This installs the app into a private venv under your home directory and adds a launcher in `~/.local/bin`:

```sh
git clone https://github.com/MechNoxer/Open-Glorious-Core.git
cd Open-Glorious-Core
./scripts/install.sh
```

After that, run:

```sh
open-glorious-core
```

If `~/.local/bin` is not in your `PATH`, run:

```sh
~/.local/bin/open-glorious-core
```

### Arch / pacman / AUR

Arch is the main distribution target for this project. The repo includes:

- `packaging/PKGBUILD`
- `packaging/.SRCINFO`
- `scripts/update-srcinfo.sh`

To build and install locally with Arch tools:

```sh
cp -r packaging /tmp/open-glorious-core-pkg
cd /tmp/open-glorious-core-pkg
makepkg -si
```

## Bugs and feedback

If something breaks, please open an issue on GitHub. Bug reports, hardware compatibility reports, and protocol notes are all useful.

## Editing the UI

The frontend is kept simple on purpose. You can edit it directly here:

- `src/open_glorious_core/static/index.html`
- `src/open_glorious_core/static/styles.css`
- `src/open_glorious_core/static/app.js`

## Feature parity vs Windows Glorious CORE

| Feature | Status | Notes |
| --- | --- | --- |
| RGB effects (all board effects), brightness, speed, colors | Implemented | Effects list, brightness and speed drive the same reports the Windows app sends. |
| Key remapping, layers, profiles | Implemented | 120-slot matrix, 3 profiles x 3 layers; writes are gated behind an explicit Apply. |
| Polling rate (1000/500/250/125 Hz) | Implemented | Performance panel; written with the dedicated `0x08` report (see Protocol notes). |
| Input latency (2/8/16 ms) | Implemented | Performance panel; carried in the properties report byte [10]. |
| Side lights (on/off) | Implemented | Performance panel toggle; properties report byte [7] (inverted: ON = 0). |
| Profile export / import | Implemented | `/api/export` downloads the stored profile/layer as JSON; `/api/import` validates and stages it — an import never writes the device, Apply still does. |
| Local persistence | Implemented | Values we write are stored under `$XDG_DATA_HOME/open-glorious-core/store.json`; the blank device mirror is covered by this store (banner says so). |
| Macros, keybind functions (incl. Win lock) | Planned (G19) | Macro report preview below. The vendor's Win-lock byte is always 0 on this board, so no Win-lock UI exists yet. |
| Per-key RGB | Planned (G20) | Needs the per-key LED matrix path. |
| Battery readout, sleep timer | Not applicable | V3-board features (properties byte [11]); the GMMK PRO 5092 wireless path does not expose them, and byte [11] is never written on this board. |
| Firmware update, cloud features | Intentionally absent | Firmware flashing risks bricking the device and cloud sync conflicts with the local-first design; neither is planned. |

## Protocol notes

All reports are feature reports on report ID `0x07`; `Data[0]` is the report ID and
`Data[1]` the sub-command. Byte `[11]` is V3-board-only and is **never** written on
the WB5092 (GMMK PRO 5092 wireless).

**Polling rate — sub-command `0x08`** (`SetPollingRatetoDevice`, 264-byte buffer):

| Byte | Meaning |
| --- | --- |
| [0] | Report ID (`0x07`) |
| [1] | Sub-command `0x08` |
| [8] | Index into `[1000, 500, 250, 125]` |
| [9] | EP2 enable — always `1` |
| [10] | LED "no change" — `1` for a standalone polling write |

**Properties report — sub-command `0x07`** (sent before every effect update):

| Byte | Meaning |
| --- | --- |
| [2] | Profile |
| [3] | Layer |
| [4] | Effect |
| [5] | Win lock — the vendor always sends `0` on this board |
| [6] | Per-key switch — always `1` |
| [7] | Side lights, **inverted**: ON = `0`, OFF = `1` |
| [8] | Brightness `x 20 / 100` |
| [10] | Input latency in raw ms (`2`/`8`/`16`) |

The GET (properties read-back) mirror is blank on the WB5092 — the board cannot
report what it currently holds. We never retry-storm it: the app shows what it
last wrote from the local store and says so honestly.

**Macro report preview (G19) — sub-command `0x05`**: 248-byte payload of
`(type, keycode, delay)` triplets, mirroring the vendor's macro editor layout.
Not implemented yet; documented here so the wire map stays in one place.
