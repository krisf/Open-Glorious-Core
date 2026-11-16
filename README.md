# Open Glorious Core

Open Glorious Core is an open-source Linux desktop app for Glorious peripherals.

Right now the project is focused on the GMMK Pro (it is the only board tested
properly so far), but the long-term goal is broader support for more Glorious
keyboards, mice, and other peripherals.

## Current status

What works today:

- device detection on Linux through `hidraw`
- stock-firmware RGB control for the GMMK Pro: all board effects, brightness,
  speed, solid colors, custom colors with picker, RGB, and hex input
- key remapping on the 120-slot matrix with profiles and layers; changes are
  staged locally and only written to the board on an explicit Apply
- performance settings: polling rate (1000/500/250/125 Hz), input latency
  (2/8/16 ms), side lights on/off
- per-key RGB colors: a per-key color map (off / lit / breathing per slot,
  plus its own brightness) stored in the local profile and re-sent to the
  board on every Apply; the board cannot report colors back, so the stored
  map is the source of truth. Lit colors are device-proven (operator gate
  2026-11-16: painted keys showed the exact colors at the correct positions);
  unpainted slots follow the active effect color on this firmware rather
  than going dark
- Windows key lock toggle: shipped and device-proven on the GMMK Pro WB —
  an operator-present physical gate (2026-11-16) confirmed the Windows key
  is suppressed while locked and works again after unlocking
- macros: a Macro Editor panel plus a "Macros" category in the remap drawer —
  up to 10 macros (M1..M10) of up to 82 steps each (key down/up with a per-step
  delay, recorded or hand-built) and three repeat modes; macro keys show an
  `M3`-style badge and are written as dedicated macro reports on Apply.
  Physical playback is device-proven (operator gate 2026-11-16: a macro
  assigned to Print Screen typed its keys into a text editor). The
  board cannot report macros back, so macros live in your profile on this
  computer — that store is the source of truth (same design as per-key colors)
- local persistence of everything the app writes, plus profile export/import
  (an import stages into the local store — it never writes the device)
- native desktop window with an editable HTML/CSS/JS frontend

What is planned later:

- keybind functions (see the parity matrix below)
- broader Glorious device support

See [Feature parity vs Windows Glorious CORE](#feature-parity-vs-windows-glorious-core)
for the full feature-by-feature status and the protocol notes.

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
| Macros | Shipped (store = truth, no readback) | Macro Editor panel + "Macros" category in the remap drawer: 10 macros (`M1`..`M10`, MacroIDs 0..9), up to 82 steps each (key down/up + 1..65535 ms delay, recorded with real timings or added by hand), three repeat modes (No Repeat / Repeat While Holding / Toggle). Assigned keys become matrix type `0x05` with code = MacroID; Apply writes cmd `0x04` (per-slot repeat-mode table) + one `0x05` per stored macro (see Protocol notes). The board has no macro readback (the GET mirror is a blank shadow), so macros live in your profile on this computer and the store is the source of truth — the UI never claims to read macros off the keyboard. Physical macro playback is **device-proven**: in the operator-present gate (2026-11-16, GMMK Pro WB, FW 0104) a 4-step macro assigned to Print Screen typed its keys into a text editor through the app's normal Apply path, and the key was restored afterwards. |
| Keybind functions | Planned | Vendor keybind-function encoding beyond plain remaps and macros is not reverse-engineered yet. |
| Windows key lock | Shipped (device-proven) | Performance panel toggle; drives the properties report byte [5] (`0` = unlocked, `1` = locked) on the next Apply. The vendor always sends `0` on this board, but the operator-present physical gate (2026-11-16, GMMK Pro WB, FW 0104) proved this firmware honors the byte: the Windows key was suppressed while locked and worked again after the unlock restore. Claim is board-specific — other firmware may differ. |
| Per-key RGB | Shipped (device-proven colors) | "Per-key colors" in the Lighting panel: up to 120 slots with off/lit/breathing modes and its own brightness, written over three cmd `0x06` layout pages on every Apply. There is no color readback (the cmd `0x81` mirror is a blank shadow), so colors live in your profile on this computer — that store is the source of truth, and the UI never claims to read colors off the keyboard. Operator gate 2026-11-16 (GMMK Pro WB): painted keys lit the exact colors at the correct positions. Caveats: unpainted (zero) slots follow the active effect color on this firmware instead of going dark, and the per-key brightness byte showed no visible effect at 20% — both recorded as unproven semantics, not defects in the byte encoding (which matches the vendor exactly). |
| Windows CORE knob assignment | Intentionally absent | Windows CORE knob assignment is a CORE-app software feature on this board (no device-protocol path on 5092 — verified against vendor sources); out of scope for the Linux app; knob keeps firmware default behavior. |
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
| [5] | Win lock — we send `1` when locked, `0` otherwise; the vendor always sends `0` on this board, but the 2026-11-16 physical gate proved this firmware honors the byte (Windows key suppressed while locked, restored on unlock) |
| [6] | Per-key switch — always `1` |
| [7] | Side lights, **inverted**: ON = `0`, OFF = `1` |
| [8] | Brightness `x 20 / 100` |
| [10] | Input latency in raw ms (`2`/`8`/`16`) |

The GET (properties read-back) mirror is blank on the WB5092 — the board cannot
report what it currently holds. We never retry-storm it: the app shows what it
last wrote from the local store and says so honestly.

**Wire length (G19-FIX2, device-proven 2026-11-16):** the three NEW G19
commands — per-key layout `0x06`, macro type table `0x04`, macro data `0x05` —
are built as 264-byte buffers but go on the wire as their FIRST 256 BYTES on
this board. Vendor rule (`SetFeatureReport`): `KeyMatrixLength <= 120` sends
256 wire bytes; only larger-matrix boards send 264. A 264-byte `0x06` report
STALLS the WB5092 (EPIPE on the hidraw feature ioctl); the 256-byte truncation
is accepted on all pages. The older commands (properties `0x07`, effect
`0x02`, keymap `0x03`, bind `0x01`, profile update, polling `0x08`) are
physically proven at 264 on this board and are sent unchanged.

**Per-key layout — sub-command `0x06`** (only sent on Apply while per-key mode
is enabled; three pages, one report each, 264-byte buffer / 256 wire bytes;
per-key key number `83`, 600-byte layout = 120 slots x 5 bytes paged in three
200-byte slices):

| Byte | Meaning |
| --- | --- |
| [2] | Profile |
| [3] | Layer |
| [4] | Key number — always `83` |
| [5] | Page (`0`, `1`, `2`) |
| [6] | Per-key brightness `x 20 / 100` |
| [8..207] | Page slice of the layout: per slot — visibility (`0x00` off / `0xFF` lit / `0xFE` breathing), slot index, R, G, B; a slot with no saved color is five zero bytes. Device caveat: on the WB5092 zero entries follow the active effect color (they do not go dark); the vendor's own comment labels `0x00` "Off", but their UI always paints every key, so zero entries never occur for real keys in the vendor flow |

Side LEDs are ordinary slots in this layout (15–18, 35–38, 55–58, 75–78) — no
special encoding. The PerKey switch byte (properties [6]) is always `1` on this
board and stays untouched; the pages only ride when per-key mode is on.

**Macro type table — sub-command `0x04`** (264-byte buffer / 256 wire bytes,
only sent on Apply when at least one key carries a macro; payload from `[8]`
is two bytes per slot, big-endian u16 at `[8 + slot * 2]`):

| Byte | Meaning |
| --- | --- |
| [2] | Profile + 1 |
| [3] | Layer + 1 |
| [8 + slot*2 .. +1] | Repeat mode for that slot: No Repeat `0x0001`, Repeat While Holding `0xFFFF`, Toggle `0x0000`; slots without a macro are `0x0000` |

**Macro data — sub-command `0x05`** (one 264-byte buffer / 256 wire bytes per
stored non-empty macro, sent on Apply in MacroID order; the 248-byte payload
`[8..255]` holds up to 82 three-byte steps — a trailing zero ends the list):

| Byte | Meaning |
| --- | --- |
| [2] | MacroID (0..9; shown as `M1`..`M10` in the UI) |
| [8 + i*3] | `(delay >> 8)` with `0x80` set while the key goes DOWN |
| [9 + i*3] | `delay & 0xFF` (big-endian milliseconds, 1..65535) |
| [10 + i*3] | HID usage code of the key |

A step emits its change **then** waits its delay. Macro keys are matrix cells
of type `0x05` with code = MacroID in the `0x03` report; everything else about
the matrix write is unchanged.

Footnote (physical gate, 2026-11-16, operator present, GMMK Pro WB 320f:5092
FW 0104): every write stage was followed by an exact restore, and the final
store matched the pre-gate baseline byte-for-byte. Proven on hardware:
win-lock byte [5] (Windows key suppressed while locked, restored on unlock);
per-key lit colors at the correct keys via the normal Apply chain; macro
playback (a 4-step "hi" macro on Print Screen typed into a text editor, then
the key was restored). Still unproven, honestly labeled: the per-key
brightness byte (a 20% probe showed no visible change) and zero-visibility
"off" semantics (unpainted slots follow the effect color on this firmware).
