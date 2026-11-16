from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import cast
from pathlib import Path

from .device import GMMK_PRO_PRODUCT_ID, GLORIOUS_VENDOR_ID


class BackendError(RuntimeError):
    """Raised when the keyboard backend cannot complete a request."""


@dataclass(slots=True)
class KeyboardState:
    brightness: int | None = None
    effect: int | None = None
    speed: int | None = None
    hue: int | None = None
    saturation: int | None = None
    red: int | None = None
    green: int | None = None
    blue: int | None = None


# Commands of the vendor report-7 protocol, decoded from Glorious CORE 2.1.22's
# own TypeScript (remap/PROTOCOL_DECODE.md; transport = FEATURE report ID 7).
CMD_CHANGE_PROFILE = 0x01  # 4 selector bytes ONLY (ChangeProfileID)
CMD_SET_LAYOUT = 0x06  # per-key LED layout WRITE (G19A; properties/effect sibling)
CMD_KEY_MATRIX = 0x03  # the keymap WRITE (SendButtonMatrix2Device)
CMD_SET_POLLING = 0x08  # SetPollingRatetoDevice (G18 P1, PROTOCOL_DECODE.md line 34)
CMD_GET_CONFIG = 0x81  # 129: config-mirror read (our proven read path)

# G19A per-key LED layout (vendor 2.1.22 ground truth, G19A_PLAN.md): the
# layout is a 600-byte buffer = 120 slots x 5 bytes; slot i lives at i*5:
#   +0 visible/CO_BR: 0x00 OFF, 0xFF lit, 0x01..0xFE breathing intensity
#   +1 the slot index itself (self-identifying byte)
#   +2..4 R, G, B
# It is pushed as 3 pages of 200 bytes each (this board: iLayerCount=3 because
# KeyMatrixLength <= 120) through the standalone 264-byte cmd-0x06 report.
# Side LEDs are ordinary slots in the SAME matrix — no special encoding.
PER_KEY_KEY_NUMBER = 83  # vendor KeyNumber constant — sent verbatim at [4]
PER_KEY_PAGE_COUNT = 3  # pages 0..2, only report byte [5] changes
PER_KEY_PAGE_BYTES = 200  # layout bytes carried per page, at report [8..207]
PER_KEY_LAYOUT_SLOTS = 120
PER_KEY_SLOT_BYTES = 5
PER_KEY_LAYOUT_BYTES = PER_KEY_LAYOUT_SLOTS * PER_KEY_SLOT_BYTES  # 600
PER_KEY_LAYOUT_REPORT_LENGTH = 264
PER_KEY_BRIGHTNESS_LEVELS = 20  # byte [6]: round(brightness_percent * 20 / 100)
# vis literal -> slot byte +0 (the vendor breathing intensity is 0xFE).
PER_KEY_VIS_BYTES = {"off": 0x00, "lit": 0xFF, "breath": 0xFE}

# G19B macros (G19B_PLAN.md, vendor Glorious CORE 2.1.22 `MacroToData` /
# arrMacroType ground truth — do NOT re-derive):
#
# * Macro data: one 264-byte feature report per macro, cmd 0x05, [2]=MacroID,
#   payload [8..255] (248 bytes) = a flat stream of 3-byte events; trailing
#   zeros ARE the end marker. Event b0 = (delay >> 8) | 0x80 when keydown,
#   b1 = delay & 0xFF (BIG-ENDIAN 16-bit ms), b2 = HID keycode from the same
#   code space as the keymap. Keydown and keyup are separate events; the
#   stream is time-sorted. 248 // 3 = 82 events maximum.
# * Macro type table: ONE 264-byte report per profile/layer, cmd 0x04,
#   [2]=profile [3]=layer (1-based, same convention as cmd 0x03), then 2
#   bytes PER SLOT (slot i at [8 + i*2], big-endian u16) looked up in
#   arrMacroType by repeat type. Vendor skips the send entirely when no cell
#   carries a macro.
# * Matrix integration: a macro-assigned cell is type 0x05 with code =
#   MacroID (vendor comment: 'key matrix type-0x05: Macro').
# There is NO macro readback (blank shadow mirror — live evidence
# 2026-11-15): the local store is truth, like per-key RGB.
CMD_MACRO_TYPE_TABLE = 0x04
CMD_MACRO_DATA = 0x05
MACRO_REPORT_LENGTH = 264  # standalone 264-byte buffers (like cmd 0x06/0x08)
MACRO_REPORT_PAYLOAD_OFFSET = 0x08
MACRO_PAYLOAD_BYTES = 248  # 264 - 8 - 8 tail margin of the vendor buffer
MACRO_EVENT_BYTES = 3
MACRO_MAX_EVENTS = MACRO_PAYLOAD_BYTES // MACRO_EVENT_BYTES  # 82
MACRO_KEYDOWN_FLAG = 0x80
MACRO_DELAY_MAX = 0xFFFF
MACRO_ID_MAX = 9  # MacroIDs 0..9 shown M1..M10 (product cap, not hardware)
MACRO_TYPE_TABLE_BYTES_PER_SLOT = 2
# arrMacroType repeat map (vendor literals, pinned by tests):
MACRO_TYPE_BY_REPEAT = {0: 0x0001, 1: 0xFFFF, 2: 0x0000}
# Wire pairs per slot ([8+i*2], [8+i*2+1]) — BIG-ENDIAN (ground-truth protocol
# section, plan lines 24-31: "big-endian u16", values 0x0001/0xFFFF/0x0000).
# NOTE ON THE PLAN'S TEST BULLET: its commit-3 line paraphrases mode 0 as
# "0 -> 01 00"; that byte order CONTRADICTS the ground-truth section (BE of
# 0x0001 is 00 01 — the modes 1/2 pairs FF FF/00 00 are order-symmetric and
# cannot distinguish the two readings). The section labeled "verified against
# Glorious CORE 2.1 extracted sources — ground truth" wins; tests pin 00 01.
MACRO_TYPE_WIRE_PAIRS = {0: (0x00, 0x01), 1: (0xFF, 0xFF), 2: (0x00, 0x00)}

# G18 P1 polling-rate wire map (vendor SetPollingRatetoDevice): a STANDALONE
# 264-byte buffer (Buffer.alloc(264)) — not the 256-byte properties framing.
# Data[0]=0x07 report ID, Data[1]=0x08 command, Data[8]=index into the rate
# tuple below, Data[9]=EP2Enable (vendor always 1), Data[10]=LEDNoChange (1 on
# a standalone send). Data[11] exists only on V3 boards and is NEVER written
# on WB5092.
POLLING_RATES = (1000, 500, 250, 125)
POLLING_REPORT_LENGTH = 264

# G18 P2: properties byte [10] carries the RAW input-latency milliseconds;
# the vendor UI offers exactly 2/8/16 ms. Byte [7] is the side-light switch
# INVERTED (lights ON -> 0, OFF -> 1).
INPUT_LATENCY_MS = (2, 8, 16)

# Keymap table location inside the 256-byte config mirror exposed by a GET of
# feature report 7 (remap/DISCOVERY.md "OPERATOR RUN"): 120 slots, one USB HID
# usage byte per physical key in row-major order, 0xFF = unassigned slot.
# CANONICAL (no-echo) offset; hidraw may echo the report ID as byte 0 and shift
# everything by one -> use locate_codes_table(), never this constant, to parse a
# live response.
KEYMAP_OFFSET = 0x80
KEYMAP_SLOTS = 120
KEYMAP_UNASSIGNED = 0xFF

# All-zero codes table = factory-reset SHADOW BUFFER (remap/PROTOCOL_DECODE.md
# G6 verdict): cmd 0x03 writes physically land, yet the GET mirror stays
# 0x00 x 120 forever — the board's mirror is push-only and never re-fills
# after reset. It is NOT a write failure; treat it as "read-back cannot
# verify", display the packaged factory defaults, and say so honestly.
KEYMAP_BLANK_TABLE = bytes(KEYMAP_SLOTS)

# cmd 0x03 KeyMatrix payload layout (SendButtonMatrix2Device, PROTOCOL_DECODE.md):
# per-key type bytes at [8..127], key codes at [128..247], both 120 bytes.
KEYMAP_TYPES_OFFSET = 0x08
KEYMAP_CODES_OFFSET = 0x80

# First four factory codes (Esc F1 F2 F3): the anchor used to locate the codes
# table inside a GET response whatever the report-ID-echo shape is.
CODES_TABLE_ANCHOR = bytes((0x29, 0x3A, 0x3B, 0x3C))
REPORT_ID_ECHO = 0x07

# G17: the G12 "EMPIRICAL caps overlay" (0x39 + type 0x01 at slot 58) is
# REMOVED. The G13 cell sweep (F13 0x68 @58 + F14 0x69 @60 written via the GUI,
# then a Caps Lock press → hidraw emitted 00 00 69 twice, nothing from 58)
# proved the physical CapsLock cell is slot 60 — exactly where the vendor
# Buttoninfo_Default already puts 0x39 with type 0x01. The factory vector is
# therefore caps-complete as shipped: a plain factory write restores CapsLock
# (G11's slot-58 claim was a misattribution — the morning recovery wrote 0x39
# to BOTH cells and the board answered via 60).

def build_macro_events_bytes(events: list[dict]) -> bytes:
    """Encode the cmd-0x05 macro byte stream (vendor `MacroToData`).

    One 3-byte event per step, EXACTLY as pinned by G19B_PLAN.md:
    ``b0 = (delay_ms >> 8) | 0x80 (keydown) else 0x00``,
    ``b1 = delay_ms & 0xFF`` (big-endian 16-bit ms delta),
    ``b2 = HID keycode`` (same code space as the keymap table).

    Policy, stated once (plan: "Delay must be >= 1 (vendor clamps zero/
    negative deltas to 1)"): THIS WRITER REJECTS delay < 1 instead of
    silently clamping — the operator's authored timing must never be
    rewritten behind their back (the API/store boundary rejects 0 too; the
    test suite states "rejected" explicitly). delay > 65535 cannot encode
    into the 16-bit big-endian field, and > 82 events cannot fit the
    248-byte payload — both raise BackendError.

    Events are dicts {"down": bool, "code": int, "delay": int}; the trailing
    ZERO padding (the end marker) is the report builder's job.
    """
    events = list(events)
    if len(events) > MACRO_MAX_EVENTS:
        raise BackendError(
            f"macro stream cap is {MACRO_MAX_EVENTS} events "
            f"({MACRO_PAYLOAD_BYTES}-byte payload), got {len(events)}."
        )
    stream = bytearray()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise BackendError(f"macro event {index} must be an object, got {event!r}.")
        down = event.get("down")
        code = event.get("code")
        delay = event.get("delay")
        if not isinstance(down, bool):
            raise BackendError(f"macro event {index}: down must be a boolean, got {down!r}.")
        if (
            not isinstance(delay, int) or isinstance(delay, bool)
            or not 1 <= delay <= MACRO_DELAY_MAX
        ):
            raise BackendError(
                f"macro event {index}: delay must be an int 1..{MACRO_DELAY_MAX} ms — "
                f"zero/negative delays are REJECTED by this writer (the vendor "
                f"clamps them to 1), got {delay!r}."
            )
        if (
            not isinstance(code, int) or isinstance(code, bool)
            or not 0x00 <= code <= 0xFE
        ):
            raise BackendError(
                f"macro event {index}: keycode must be 0x00..0xFE (0xFF is "
                f"'unassigned', never a macro key), got {code!r}."
            )
        stream.append(((delay >> 8) | (MACRO_KEYDOWN_FLAG if down else 0x00)) & 0xFF)
        stream.append(delay & 0xFF)
        stream.append(code & 0xFF)
    return bytes(stream)


def build_macro_type_table_bytes(assignments: dict[int, int]) -> bytes:
    """Encode the cmd 0x04 payload: 2 bytes per slot (240 bytes).

    ``assignments`` maps slot -> repeat type (0 No Repeat, 1 Repeat While
    Holding, 2 Toggle); the pair emitted at [i*2] is the big-endian
    arrMacroType literal (0x0001 -> 00 01, 0xFFFF -> FF FF, 0x0000 -> 00 00
    — see the MACRO_TYPE_WIRE_PAIRS note for the plan's contradictory test
    bullet). Unassigned slots are 00 00. An EMPTY mapping yields the all-zero
    table — callers SKIP the send in that case (vendor: the buffer is built
    only when at least one MacroFunction cell exists).
    """
    table = bytearray(KEYMAP_SLOTS * MACRO_TYPE_TABLE_BYTES_PER_SLOT)
    for slot, repeat in assignments.items():
        if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < KEYMAP_SLOTS:
            raise BackendError(f"macro type table: slot must be 0..{KEYMAP_SLOTS - 1}, got {slot!r}.")
        try:
            low_high = MACRO_TYPE_WIRE_PAIRS[repeat]
        except (KeyError, TypeError):
            raise BackendError(
                f"macro type table slot {slot}: repeat must be 0/1/2 "
                f"(arrMacroType), got {repeat!r}."
            ) from None
        offset = slot * MACRO_TYPE_TABLE_BYTES_PER_SLOT
        table[offset] = low_high[0]
        table[offset + 1] = low_high[1]
    return bytes(table)


def locate_codes_table(response: bytes | bytearray) -> int:
    """Offset of the 120-byte codes table inside a GET config-mirror response.

    Two shapes are seen on this protocol (remap/DISCOVERY.md, operator runs
    2-3): probe-era captures stored the payload alone (table at 0x80) and the
    live ``_exchange_feature_report`` path gets the 0x07 report ID echoed as
    byte 0, shifting every payload byte by one (table at 0x81). Resolve it
    robustly, in order:

    1. factory anchor - the codes table starts with the factory keycodes
       ``29 3A 3B 3C`` (Esc F1 F2 F3); test the echo-aware candidates first;
    2. anchor scan anywhere in the buffer (covers a shifted/odd framing);
    3. fall back to the plain slice, 0x80, or 0x81 when byte 0 is the echoed
       report ID - a remapped Esc breaks the anchor, the echo shape does not
       move the table.

    Returns the offset; callers slice ``response[off : off + KEYMAP_SLOTS]``.
    """
    buf = bytes(response)
    echo = 1 if buf[:1] == bytes((REPORT_ID_ECHO,)) else 0
    candidates = (KEYMAP_OFFSET + echo, KEYMAP_OFFSET, KEYMAP_OFFSET + 1)
    for offset in dict.fromkeys(candidates):
        if buf[offset : offset + len(CODES_TABLE_ANCHOR)] == CODES_TABLE_ANCHOR:
            return offset
    anchor = buf.find(CODES_TABLE_ANCHOR)
    if anchor != -1:
        return anchor
    return KEYMAP_OFFSET + echo

# slot -> physical key name for the GMMK Pro 75 % board (full derivation:
# remap/KEYMAP_MAP.md + its SWEEP-VERDICT note). Slots 14 / 72 / 92 carry
# non-FF raw bytes in the factory dump but have no confirmed keycap on this unit
# and are deliberately unlabeled. G17 (retracting G11): Caps Lock sits at slot
# 60 — the vendor Buttoninfo_Default's OWN cell for it (0x39, type 0x01), now
# sweep-confirmed on the operator's WB5092 firmware (the G13 F14 capture).
# Slot 58 is a dead matrix spare among the row-4 head cells 55-59 (factory
# 0xFF, no keycap); it carries no label and is hidden from the grid.
KEYMAP_SLOT_LABELS: dict[int, str] = {
    **{0: "Esc"},
    **{i: f"F{i}" for i in range(1, 13)},
    13: "PrtSc",
    20: "Grave",
    **{i: str((i - 20) % 10) for i in range(21, 31)},
    31: "Minus",
    32: "Equal",
    33: "Backspace",
    34: "Delete",
    40: "Tab",
    **{41 + i: c for i, c in enumerate("QWERTYUIOP")},
    51: "LBracket",
    52: "RBracket",
    53: "Backslash",
    54: "PageUp",
    60: "Caps Lock",
    **{61 + i: c for i, c in enumerate("ASDFGHJKL")},
    70: "Semicolon",
    71: "Quote",
    73: "Enter",
    74: "PageDown",
    80: "Left Shift",
    **{81 + i: c for i, c in enumerate("ZXCVBNM")},
    88: "Comma",
    89: "Period",
    90: "Slash",
    91: "Right Shift",
    93: "Up",
    94: "End",
    100: "Left Ctrl",
    101: "Left Win",
    102: "Left Alt",
    105: "Space",
    108: "Right Alt",
    109: "Fn",
    110: "Right Ctrl",
    112: "Left",
    113: "Down",
    114: "Right",
}


@dataclass(slots=True)
class KeymapEntry:
    """One entry of the stock onboard keymap.

    ``slot`` is the position in the device keymap table (0-119, see
    remap/KEYMAP_MAP.md); ``scancode`` is the raw keymap byte — the USB HID
    keyboard usage ID for standard keys, vendor codes for a few specials
    (e.g. 0xA0 = Fn), 0xFF = unassigned; ``label`` is a UI hint only.
    """

    slot: int
    scancode: int
    label: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.slot, int) or not 0 <= self.slot < KEYMAP_SLOTS:
            raise BackendError(f"Keymap slot must be 0-{KEYMAP_SLOTS - 1}, got {self.slot!r}.")
        if not isinstance(self.scancode, int) or not 0 <= self.scancode <= 0xFF:
            raise BackendError(f"Keymap scancode must be a byte 0x00-0xFF, got {self.scancode!r}.")


class BaseBackend:
    COLOR_NAMES = ("red", "yellow", "green", "cyan", "blue", "purple", "white")
    EFFECT_COLOR_GROUPS: dict[int, list[str]] = {}
    name = "backend"

    def is_available(self) -> bool:
        return True

    def install_hint(self) -> str:
        return ""

    def read_state(self) -> KeyboardState:
        raise NotImplementedError

    def set_brightness(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_effect(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_speed(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_hue(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_saturation(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_color_name(self, name: str, save: bool) -> str:
        raise NotImplementedError

    def set_rgb_color(self, red: int, green: int, blue: int, save: bool, group: str | None = None) -> str:
        raise NotImplementedError

    def set_polling_rate(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_input_latency(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_side_lights(self, value: bool, save: bool) -> str:
        raise NotImplementedError

    def set_win_lock(self, value: bool) -> str:
        raise NotImplementedError

    def set_per_key_enabled(self, value: bool) -> str:
        raise NotImplementedError

    def set_per_key_brightness(self, value: int) -> str:
        raise NotImplementedError

    def set_per_key_color(self, slot: int, rgb: object, vis: object = None) -> str:
        raise NotImplementedError

    def clear_per_key_slot(self, slot: int) -> str:
        raise NotImplementedError

    def device_indication(self) -> str:
        raise NotImplementedError

    def save(self) -> str:
        raise NotImplementedError


class QmkHidBackend(BaseBackend):
    name = "qmk_hid"

    def __init__(self) -> None:
        self.binary = shutil.which("qmk_hid")

    def is_available(self) -> bool:
        return self.binary is not None

    def install_hint(self) -> str:
        return (
            "Install qmk_hid first. On Arch, the easiest route is usually to build "
            "FrameworkComputer/qmk_hid from source or use a package helper if you "
            "prefer the AUR."
        )

    def read_state(self) -> KeyboardState:
        return KeyboardState(
            brightness=self._read_value("--rgb-brightness"),
            effect=self._read_value("--rgb-effect"),
            speed=self._read_value("--rgb-effect-speed"),
            hue=self._read_value("--rgb-hue"),
            saturation=self._read_value("--rgb-saturation"),
        )

    def set_brightness(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-brightness", str(value), *self._save_args(save))

    def set_effect(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-effect", str(value), *self._save_args(save))

    def set_speed(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-effect-speed", str(value), *self._save_args(save))

    def set_hue(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-hue", str(value), *self._save_args(save))

    def set_saturation(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-saturation", str(value), *self._save_args(save))

    def set_color_name(self, name: str, save: bool) -> str:
        if name not in self.COLOR_NAMES:
            raise BackendError(f"Unsupported color preset: {name}")
        return self._run_via("--rgb-color", name, *self._save_args(save))

    def set_rgb_color(self, red: int, green: int, blue: int, save: bool, group: str | None = None) -> str:
        raise BackendError("Custom RGB values are not mapped for the qmk_hid fallback yet.")

    def set_polling_rate(self, value: int, save: bool) -> str:
        raise BackendError("Polling rate control is not mapped for the qmk_hid fallback yet.")

    def set_input_latency(self, value: int, save: bool) -> str:
        raise BackendError("Input latency control is not mapped for the qmk_hid fallback yet.")

    def set_side_lights(self, value: bool, save: bool) -> str:
        raise BackendError("Side light control is not mapped for the qmk_hid fallback yet.")

    def device_indication(self) -> str:
        return self._run_via("--device-indication")

    def save(self) -> str:
        return self._run_via("--save")

    def _read_value(self, option: str) -> int | None:
        output = self._run_via(option)
        numbers = [int(match) for match in re.findall(r"\b\d+\b", output)]
        return numbers[-1] if numbers else None

    def _save_args(self, save: bool) -> tuple[str, ...]:
        return ("--save",) if save else ()

    def _run_via(self, *args: str) -> str:
        if not self.binary:
            raise BackendError(self.install_hint())

        cmd = [
            self.binary,
            "--vid",
            GLORIOUS_VENDOR_ID,
            "--pid",
            GMMK_PRO_PRODUCT_ID,
            "via",
            *args,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "Unknown qmk_hid error"
            raise BackendError(details)
        return result.stdout.strip() or "Done."


class _AutoDetect:
    """Sentinel type: hidraw unset -> auto-detect the vendor interface (legacy default).

    Kept distinct from ``None`` so callers (unit tests) can pass an explicit
    ``hidraw=None`` meaning "no device, do not auto-detect, do no device IO".
    """


_AUTO_DETECT = _AutoDetect()


class StockGmmkProBackend(BaseBackend):
    name = "stock-hidraw-glorious"
    REPORT_ID = 7
    REPORT_LENGTH = 256
    SOLID_EFFECT = 7
    EFFECT_COLOR_GROUPS = {
        6: ["Primary"],
        7: ["Primary"],
    }
    MULTICOLOR_EFFECTS = {1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18}
    EFFECT_LABELS = {
        0: "LED Off",
        1: "Glorious Mode",
        2: "Wave 1",
        3: "Wave 2",
        4: "Spiraling Wave",
        5: "Acid Mode",
        6: "Breathing",
        7: "Normally On",
        8: "Ripple Graff",
        9: "Pass Without Trace",
        10: "Fast Run Without Trace",
        11: "Matrix 2",
        12: "Matrix 3",
        13: "Rainbow",
        14: "Heartbeat Sensor",
        15: "Digital Times",
        16: "Kamehemeha",
        17: "Pingpong",
        18: "Surmount",
    }
    COLOR_MAP = {
        "red": (255, 0, 0),
        "yellow": (255, 255, 0),
        "green": (0, 255, 0),
        "cyan": (0, 255, 255),
        "blue": (0, 0, 255),
        "purple": (128, 0, 255),
        "white": (255, 255, 255),
    }

    def __init__(self, hidraw: Path | None | _AutoDetect = _AUTO_DETECT) -> None:
        self.hidraw: Path | None = (
            self._detect_stock_interface() if isinstance(hidraw, _AutoDetect) else hidraw
        )
        self.state = KeyboardState(brightness=100, effect=7, speed=50, red=255, green=255, blue=255)
        self.color_name = "white"
        self.color_rgb = (255, 255, 255)
        # G18 settings parity. These defaults keep the wire behavior
        # BYTE-IDENTICAL to pre-G18 (P3): 1000 Hz is cmd-0x08 index 0, side
        # lights ON maps to properties [7]=0 (inverted), and 16 ms is the
        # value [10] was hardcoded to.
        self.polling_rate = 1000
        self.input_latency_ms = 16
        self.side_lights = True
        # G19A Win-lock: properties byte [5], 0 = unlocked / 1 = locked.
        # DEFAULT UNLOCKED keeps the emitted wire BYTE-IDENTICAL to pre-G19A.
        # Board support is pending the physical gate; this is honest plumbing,
        # not a verified device feature.
        self.win_lock = False
        # G19A per-key RGB. THE LOCAL STORE IS THE SOURCE OF TRUTH: the
        # cmd-129 config mirror is blank-shadow on this board (live evidence
        # 2026-11-15: 256 zero bytes) — there is NO LED state readback, ever.
        # Shape (mirrored 1:1 into the store settings):
        #   {"enabled": bool, "brightness": 0..100,
        #    "slots": {str(slot): {"vis": "off|lit|breath", "rgb": [r, g, b]}}}
        self.per_key: dict = {"enabled": False, "brightness": 100, "slots": {}}
        self.profile = 1
        self.layer = 1
        if self.hidraw is not None:
            self._sync_active_profile_layer()

    def is_available(self) -> bool:
        return self.hidraw is not None

    def install_hint(self) -> str:
        return (
            "Stock firmware support needs access to the GMMK Pro hidraw feature-report interface. "
            "Make sure the udev rule is loaded and you can read/write the matching /dev/hidraw node."
        )

    def read_state(self) -> KeyboardState:
        if not self.hidraw:
            raise BackendError(self.install_hint())
        self._sync_active_profile_layer()
        return KeyboardState(
            brightness=self.state.brightness,
            effect=self.state.effect,
            speed=self.state.speed,
            hue=None,
            saturation=None,
            red=self.color_rgb[0],
            green=self.color_rgb[1],
            blue=self.color_rgb[2],
        )

    def set_brightness(self, value: int, save: bool) -> str:
        self.state.brightness = max(0, min(100, int(value)))
        self._apply_current_state()
        return f"Applied stock RGB brightness {self.state.brightness} on {self.hidraw.name}."

    def set_effect(self, value: int, save: bool) -> str:
        effect = int(value)
        if effect not in self.EFFECT_LABELS:
            raise BackendError("Stock firmware currently supports effect IDs 0-18 for the GMMK Pro.")
        self.state.effect = effect
        self._apply_current_state()
        return f"Applied stock RGB effect {effect} ({self.EFFECT_LABELS[effect]}) on {self.hidraw.name}."

    def set_speed(self, value: int, save: bool) -> str:
        self.state.speed = max(0, min(100, int(value)))
        self._apply_current_state()
        return f"Applied stock RGB speed {self.state.speed} on {self.hidraw.name}."

    def set_hue(self, value: int, save: bool) -> str:
        raise BackendError("Hue control is not mapped yet for stock firmware. Use the color preset buttons for now.")

    def set_saturation(self, value: int, save: bool) -> str:
        raise BackendError("Saturation control is not mapped yet for stock firmware. Use the color preset buttons for now.")

    def set_color_name(self, name: str, save: bool) -> str:
        if name not in self.COLOR_MAP:
            raise BackendError(f"Unsupported color preset: {name}")
        self.color_name = name
        self.color_rgb = self.COLOR_MAP[name]
        self.state.red, self.state.green, self.state.blue = self.color_rgb
        if not self._effect_supports_color():
            self.state.effect = self.SOLID_EFFECT
        self._apply_current_state()
        return f"Applied stock RGB color {name} on {self.hidraw.name}."

    def set_rgb_color(self, red: int, green: int, blue: int, save: bool, group: str | None = None) -> str:
        self.color_name = "custom"
        self.color_rgb = (
            max(0, min(255, int(red))),
            max(0, min(255, int(green))),
            max(0, min(255, int(blue))),
        )
        self.state.red, self.state.green, self.state.blue = self.color_rgb
        if not self._effect_supports_color():
            self.state.effect = self.SOLID_EFFECT
        self._apply_current_state()
        return f"Applied custom RGB color #{self.color_rgb[0]:02X}{self.color_rgb[1]:02X}{self.color_rgb[2]:02X} on {self.hidraw.name}."

    def set_polling_rate(self, value: int, save: bool) -> str:
        """SetPollingRatetoDevice (cmd 0x08, G18 P1) — standalone 264-byte report.

        The vendor sends this as its own command (never part of the
        properties/effective/profile trio), so the buffer goes straight
        through the feature-report path exactly like the cmd-0x01 bind:
        [0]=0x07, [1]=0x08, [8]=rate index into POLLING_RATES,
        [9]=EP2Enable (always 1), [10]=LEDNoChange (1: keep current LEDs),
        everything else zero. Byte [11] is V3-boards-only and is NEVER
        written on WB5092. State updates only after the write succeeds.
        """
        if isinstance(value, bool):
            raise BackendError(f"Polling rate must be one of {POLLING_RATES}, got {value!r}.")
        try:
            rate = int(value)
        except (TypeError, ValueError) as exc:
            raise BackendError(f"Polling rate must be one of {POLLING_RATES}, got {value!r}.") from exc
        if rate not in POLLING_RATES:
            raise BackendError(f"Polling rate must be one of {POLLING_RATES}, got {rate}.")
        if not self.hidraw:
            raise BackendError(self.install_hint())
        report = bytearray(POLLING_REPORT_LENGTH)
        report[0] = self.REPORT_ID
        report[1] = CMD_SET_POLLING
        report[8] = POLLING_RATES.index(rate)
        report[9] = 1  # EP2Enable: the vendor always sends 1
        report[10] = 1  # LEDNoChange: standalone change leaves the LEDs alone
        self._send_feature_report(report)
        self.polling_rate = rate
        return f"Applied USB polling rate {rate} Hz on {self.hidraw.name}."

    def set_input_latency(self, value: int, save: bool) -> str:
        """Debounce window in ms — properties byte [10] carries the RAW value (P2)."""
        if isinstance(value, bool):
            raise BackendError(f"Input latency must be one of {INPUT_LATENCY_MS} ms, got {value!r}.")
        try:
            latency = int(value)
        except (TypeError, ValueError) as exc:
            raise BackendError(f"Input latency must be one of {INPUT_LATENCY_MS} ms, got {value!r}.") from exc
        if latency not in INPUT_LATENCY_MS:
            raise BackendError(
                f"Input latency must be one of {INPUT_LATENCY_MS} ms, got {latency}."
            )
        self.input_latency_ms = latency
        self._apply_current_state()
        return f"Applied input latency {latency} ms on {self.hidraw.name}."

    def set_side_lights(self, value: bool, save: bool) -> str:
        """Side light bar on/off — properties byte [7] is INVERTED (P2): ON→0, OFF→1."""
        if not isinstance(value, bool):
            raise BackendError(f"Side lights switch must be true or false, got {value!r}.")
        self.side_lights = value
        self._apply_current_state()
        state = "on" if value else "off"
        return f"Side lights turned {state} on {self.hidraw.name}."

    # -- G19A: Win-lock flag + per-key RGB (local-mirror state) --------------
    #
    # These setters NEVER write the device: they update the in-memory state
    # the controller persists to the local store. The wire effect happens on
    # the NEXT lighting Apply (_apply_current_state): win-lock rides the
    # properties report byte [5], the per-key colors ride the three cmd-0x06
    # layout pages. There is NO readback on this board (blank-shadow mirror)
    # — this state, persisted locally, is the source of truth.

    def set_win_lock(self, value: bool) -> str:
        """Win-lock flag for properties byte [5] (0 = unlocked, 1 = locked).

        Vendor ground truth: Glorious CORE hardcodes 0x00 here
        (GmmkSeries.ts SetLEDTypeToDevice: `Data[5] = 0x00; //Win_Lock`);
        board support is PENDING THE PHYSICAL GATE. No device write here —
        the flag reaches the keyboard with the next Apply.
        """
        if not isinstance(value, bool):
            raise BackendError(f"Win-lock switch must be true or false, got {value!r}.")
        self.win_lock = value
        state = "locked" if value else "unlocked"
        return (
            f"Windows key {state} locally — reaches the keyboard with the "
            "next lighting Apply (no device readback)."
        )

    def set_per_key_enabled(self, value: bool) -> str:
        """Per-key mode switch: only when enabled do the layout pages join
        the Apply flow (properties -> effect -> layout pages -> profile)."""
        if not isinstance(value, bool):
            raise BackendError(f"Per-key mode switch must be true or false, got {value!r}.")
        self.per_key["enabled"] = value
        state = "enabled" if value else "disabled"
        return (
            f"Per-key mode {state} locally — colors reach the keyboard with "
            "the next lighting Apply (saved on this computer; no device readback)."
        )

    def set_per_key_brightness(self, value: int) -> str:
        """Per-key global brightness percent (layout report byte [6])."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise BackendError(f"Per-key brightness must be an integer 0-100, got {value!r}.")
        if not 0 <= value <= 100:
            raise BackendError(f"Per-key brightness must be 0-100, got {value}.")
        self.per_key["brightness"] = value
        return (
            f"Per-key brightness saved at {value}% — reaches the keyboard "
            "with the next lighting Apply (no device readback)."
        )

    def set_per_key_color(self, slot: int, rgb: object, vis: object = None) -> str:
        """One slot entry: create with default vis 'lit', or update IN PLACE
        (rgb set, existing vis preserved — breathing stays breathing) unless
        an explicit vis is given. Absent slots encode 5 zero bytes; entries
        always carry the slot index at +1."""
        slot_int = self._per_key_slot(slot)
        color = self._per_key_rgb(rgb)
        vis_name = None if vis is None else self._per_key_vis(vis)
        key = str(slot_int)
        entry = self.per_key["slots"].get(key)
        if entry is None:
            self.per_key["slots"][key] = {
                "vis": vis_name if vis_name is not None else "lit",
                "rgb": color,
            }
        else:
            entry["rgb"] = color
            if vis_name is not None:
                entry["vis"] = vis_name
        return (
            f"Slot {slot_int} color saved locally — reaches the keyboard "
            "with the next lighting Apply (no device readback)."
        )

    def clear_per_key_slot(self, slot: int) -> str:
        """Remove a slot entry: absent slots encode as 5 zero bytes."""
        slot_int = self._per_key_slot(slot)
        self.per_key["slots"].pop(str(slot_int), None)
        return (
            f"Slot {slot_int} cleared locally — reaches the keyboard with "
            "the next lighting Apply (no device readback)."
        )

    @staticmethod
    def _per_key_slot(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise BackendError(
                f"Per-key slot must be an integer 0-{PER_KEY_LAYOUT_SLOTS - 1}, got {value!r}."
            )
        if not 0 <= value < PER_KEY_LAYOUT_SLOTS:
            raise BackendError(
                f"Per-key slot must be 0-{PER_KEY_LAYOUT_SLOTS - 1}, got {value}."
            )
        return int(value)

    @staticmethod
    def _per_key_rgb(value: object) -> list[int]:
        if (
            not isinstance(value, (list, tuple))
            or len(value) != 3
            or any(isinstance(ch, bool) or not isinstance(ch, int) for ch in value)
            or not all(0 <= ch <= 255 for ch in value)
        ):
            raise BackendError(
                "Per-key color must be three bytes 0-255 [r, g, b], "
                f"got {value!r}."
            )
        return [int(ch) for ch in value]

    @staticmethod
    def _per_key_vis(value: object) -> str:
        if not isinstance(value, str) or value not in PER_KEY_VIS_BYTES:
            raise BackendError(
                "Per-key visibility must be one of "
                f"{sorted(PER_KEY_VIS_BYTES)}, got {value!r}."
            )
        return value

    def build_per_key_layout(self) -> bytes:
        """The 600-byte vendor layout buffer: 120 slots x 5 bytes at i*5.

        Entry slot: +0 = vis byte (off 0x00 / lit 0xFF / breath 0xFE),
        +1 = the slot index itself, +2..4 = R, G, B. Absent slot: 5 zero
        bytes. Side LEDs are ordinary slots here — no special encoding.
        """
        layout = bytearray(PER_KEY_LAYOUT_BYTES)
        for slot_key, entry in self.per_key["slots"].items():
            slot = self._per_key_slot(int(slot_key))
            base = slot * PER_KEY_SLOT_BYTES
            layout[base] = PER_KEY_VIS_BYTES[entry["vis"]]
            layout[base + 1] = slot
            layout[base + 2 : base + 5] = self._per_key_rgb(entry["rgb"])
        return bytes(layout)

    def _prepare_per_key_layout_buffer(self, report: bytearray, page: int) -> None:
        """Vendor per-key layout report (cmd 0x06, standalone 264-byte
        buffer): [2]=profile+1-style selectors identical to the cmd-0x03
        writer, [4]=KeyNumber 83, [5]=page 0..2, [6]=global brightness
        0..20, [8..207]=the 200-byte page slice of the 600-byte layout."""
        if not 0 <= int(page) < PER_KEY_PAGE_COUNT:
            raise BackendError(
                f"Per-key layout page must be 0-{PER_KEY_PAGE_COUNT - 1}, got {page!r}."
            )
        layout = self.build_per_key_layout()
        report[0] = self.REPORT_ID
        report[1] = CMD_SET_LAYOUT
        report[2] = self.profile
        report[3] = self.layer
        report[4] = PER_KEY_KEY_NUMBER
        report[5] = int(page)
        report[6] = round(
            self.per_key["brightness"] * PER_KEY_BRIGHTNESS_LEVELS / 100
        )
        start = int(page) * PER_KEY_PAGE_BYTES
        report[8 : 8 + PER_KEY_PAGE_BYTES] = layout[start : start + PER_KEY_PAGE_BYTES]

    def apply_per_key_layout(self) -> str:
        """Push the layout: pages 0, 1, 2 sequentially, same report shape,
        only byte [5] changes (vendor iLayerCount=3 on this board)."""
        for page in range(PER_KEY_PAGE_COUNT):
            page_report = bytearray(PER_KEY_LAYOUT_REPORT_LENGTH)
            self._prepare_per_key_layout_buffer(page_report, page)
            self._send_feature_report(page_report)
        return (
            f"Sent the per-key layout in {PER_KEY_PAGE_COUNT} pages "
            "(colors come from this computer's store — the board has no readback)."
        )

    def device_indication(self) -> str:
        raise BackendError("Device indication is not mapped yet for stock firmware.")

    def save(self) -> str:
        self._apply_current_state()
        return "Re-sent the current stock RGB state to the keyboard."

    # -- Keymap (Phase 2, G5: decoded write path) ---------------------------
    #
    # READ path is PROVEN (remap/DISCOVERY.md): a GET of feature report 7
    # mirrors the ACTIVE profile's 256-byte config buffer; the codes table is
    # located with locate_codes_table() (0x80, or 0x81 behind an echoed report
    # ID) and is 120 bytes of one USB HID usage byte per slot.
    #
    # WRITE path is the vendor protocol read directly out of Glorious CORE
    # 2.1.22's own TypeScript (remap/PROTOCOL_DECODE.md): cmd 0x03 KeyMatrix
    # carries the WHOLE matrix in one report — selectors at [2]/[3], per-key
    # TYPES at [8..127], CODES at [128..247]. No auth, no unlock, no handshake.
    #
    # The Phase-2 "H1" hypothesis (cmd 0x01 = full-config write) is REFUTED on
    # device (remap/DISCOVERY.md operator run 3): cmd 0x01 is ChangeProfile,
    # FOUR selector bytes, and the firmware ignores everything past [3]. That
    # builder is gone; cmd 1 keeps strictly ChangeProfile semantics here
    # (change_profile / _prepare_change_profile_buffer).
    #
    # CORE's source indexes profiles/layers from 0 and sends `index + 1`; this
    # API is 1-based, exactly like the config-mirror header (profile 1 / layer 1
    # reads 01 01), so the wire selector bytes equal the values callers pass.

    def read_keymap(self, profile: int = 1, layer: int = 1) -> list[KeymapEntry]:
        """Return all 120 keymap slots of the active profile/layer mirror."""
        mirror = self._read_config_mirror(profile, layer)
        table = self._codes_table(mirror)
        return [
            KeymapEntry(slot=slot, scancode=byte, label=KEYMAP_SLOT_LABELS.get(slot))
            for slot, byte in enumerate(table)
        ]

    def set_keymap_codes(
        self,
        profile: int,
        layer: int,
        codes: bytes | bytearray | list[int],
        types: bytes | bytearray | list[int] | None = None,
    ) -> str:
        """Write the FULL 120-slot key matrix with cmd 0x03 (KeyMatrix).

        ``codes`` is the complete table (120 bytes, one HID usage byte per
        slot); ``types`` is the matching 120-byte per-key type array and
        defaults to the packaged vendor ``data/default_types.bin``
        (Buttoninfo_Default types: 0x01 normal x77, 0x02 x7 = the modifiers,
        0x03 consumer x1 = the knob/mute, 0xFF unused x35; PROTOCOL_DECODE.md).
        The config mirror is read first — a parsable mirror only exposes the
        ACTIVE profile, so writing a non-active slot is refused instead of
        silently hitting the wrong slot (G8: a fully BLANK mirror is the
        shadow-only state and is NOT refused — the target pair is taken as
        given) — then ONE report is sent (never retried) and the mirror
        re-read once to byte-compare the codes table.

        Types are not part of the GET mirror, so only the codes are verifiable
        read-back; that limitation is stated in the returned message. When the
        mirror is the blank factory-reset shadow buffer the write still landed
        (PROTOCOL_DECODE.md G6) and the message carries the shadow caveat
        instead of a verification claim.
        """
        codes_bytes = self._matrix_bytes("Keymap codes", codes)
        types_bytes = (
            self._default_types() if types is None else self._matrix_bytes("Keymap types", types)
        )
        # Shadow-tolerant active-slot guard + device presence (G8): a blank
        # mirror never populates on the live WB board — send, don't refuse.
        self._read_config_mirror_shadow_tolerant(profile, layer)
        shadow = self._write_keymap_matrix(codes_bytes, types_bytes, profile, layer)
        if shadow:
            return self._shadow_write_message(
                f"the full {KEYMAP_SLOTS}-slot key matrix", profile, layer
            )
        return (
            f"Wrote the full {KEYMAP_SLOTS}-slot key matrix to profile {profile} layer {layer} "
            "(cmd 3 KeyMatrix); read-back verified against the full codes table "
            "(types are not mirrored by GET and cannot be read back)."
        )

    def set_keymap(
        self,
        entries: list[KeymapEntry],
        profile: int = 1,
        layer: int = 1,
        types: bytes | bytearray | list[int] | None = None,
    ) -> str:
        """Write the given keymap slots onto the current table and persist.

        Read the mirror, patch the slots, then send the whole patched
        matrix through cmd 0x03 (the vendor protocol has no per-key write).
        A parsable mirror enforces the active-pair guard; a fully blank
        (shadow) mirror is the G8 shadow-only state — the patch is applied
        over the blank table and sent, never refused.
        """
        entries = list(entries)
        if not entries:
            raise BackendError("set_keymap requires at least one KeymapEntry.")
        seen: set[int] = set()
        for entry in entries:
            if not isinstance(entry, KeymapEntry):
                raise BackendError(f"Not a KeymapEntry: {entry!r}")
            if entry.slot in seen:
                raise BackendError(f"Duplicate keymap slot {entry.slot} in entries.")
            seen.add(entry.slot)
        mirror = self._read_config_mirror_shadow_tolerant(profile, layer)
        codes = bytearray(self._codes_table(mirror))
        for entry in entries:
            codes[entry.slot] = entry.scancode
        types_bytes = (
            self._default_types() if types is None else self._matrix_bytes("Keymap types", types)
        )
        shadow = self._write_keymap_matrix(bytes(codes), types_bytes, profile, layer)
        if shadow:
            return self._shadow_write_message(
                f"{len(entries)} keymap change(s)", profile, layer
            )
        return (
            f"Applied {len(entries)} keymap change(s) on profile {profile} layer {layer}; "
            "read-back verified against the full codes table."
        )

    def reset_keymap(self, profile: int = 1, layer: int = 1) -> str:
        """Restore factory defaults: the packaged vendor codes + vendor types,
        byte for byte, with no overlay.

        There is no dedicated reset command: "reset" means writing the captured
        factory table back with the same cmd-0x03 matrix write, using the
        vendor Buttoninfo_Default types array. The table written comes from the
        shipped dumps, never from what the device mirrors; the mirror is still
        read first so a write to a NON-active profile/layer is refused (G8: a
        fully blank shadow mirror is the shadow-only state — never refused).

        G17 (retracts G12 item 5): the G13 cell sweep proved the CapsLock cell
        is slot 60, where the shipped factory table ALREADY carries 0x39 with
        type 0x01, and the post-sweep factory restore brought CapsLock back on
        the live board. The raw vendor vector is caps-complete, so the G12
        "empirical" overlay at slot 58 (0x39 + type 0x01) is dead code against
        a dead spare and is gone: a reset is the plain vendor factory table.
        """
        # Shadow-tolerant active-slot guard + device presence (G8).
        self._read_config_mirror_shadow_tolerant(profile, layer)
        codes = self._default_keymap_table()
        types = self._default_types()
        shadow = self._write_keymap_matrix(codes, types, profile, layer)
        if shadow:
            return self._shadow_write_message("the factory default keymap", profile, layer)
        return (
            f"Restored the factory default keymap on profile {profile} layer {layer}; "
            "read-back verified against the full codes table."
        )

    # -- Macros (G19B: cmd 0x04 type table + cmd 0x05 macro data) -----------
    #
    # Byte-exact against G19B_PLAN.md (vendor MacroToData / arrMacroType).
    # Like per-key RGB, these are WRITE-only pushes: the blank shadow mirror
    # never reports macros back, so no GET-verify step exists here and the
    # messages never claim readback. One send each, never retried.

    def _build_macro_type_table_report(
        self, profile: int, layer: int, assignments: dict[int, int]
    ) -> bytearray:
        """cmd 0x04 macro type table: [2]/[3] selectors, then 2 BE bytes per
        slot at [8..247] (slot i at [8 + i*2]) from build_macro_type_table_bytes.

        Selector PINS (plan): ``[2] = profile + 1``, ``[3] = layer + 1`` —
        profile 2 / layer 3 lands 3 / 4 on the wire (the vendor macro-table
        send indexes the config array 0-based; unlike cmd 0x03, whose 1-based
        selectors go out unchanged, this buffer carries index+1).
        """
        table = build_macro_type_table_bytes(assignments)
        report = bytearray(MACRO_REPORT_LENGTH)  # 264-byte standalone buffer
        report[0] = self.REPORT_ID
        report[1] = CMD_MACRO_TYPE_TABLE
        report[2] = self._selector(profile, "profile") + 1
        report[3] = self._selector(layer, "layer") + 1
        report[
            MACRO_REPORT_PAYLOAD_OFFSET : MACRO_REPORT_PAYLOAD_OFFSET + len(table)
        ] = table
        return report

    def _build_macro_data_report(self, macro_id: int, events: list[dict]) -> bytearray:
        """cmd 0x05 macro data: [2]=MacroID (int), event stream at [8..],
        everything after the stream stays ZERO (the vendor end marker)."""
        if not isinstance(macro_id, int) or isinstance(macro_id, bool) or not 0 <= macro_id <= MACRO_ID_MAX:
            raise BackendError(
                f"MacroID must be an int 0..{MACRO_ID_MAX} (M1..M{MACRO_ID_MAX + 1}), "
                f"got {macro_id!r}."
            )
        stream = build_macro_events_bytes(events)
        report = bytearray(MACRO_REPORT_LENGTH)  # 264-byte standalone buffer
        report[0] = self.REPORT_ID
        report[1] = CMD_MACRO_DATA
        report[2] = macro_id
        report[MACRO_REPORT_PAYLOAD_OFFSET : MACRO_REPORT_PAYLOAD_OFFSET + len(stream)] = stream
        return report

    def write_macro_type_table(self, profile: int, layer: int, assignments: dict[int, int]) -> str:
        """Send the macro type table (cmd 0x04) once. Callers must skip this
        send when NOTHING is macro-assigned (vendor behavior, plan-pinned)."""
        if not self.hidraw:
            raise BackendError(self.install_hint())
        report = self._build_macro_type_table_report(profile, layer, assignments)
        self._send_feature_report(report)
        return (
            f"Sent the macro type table for profile {profile} layer {layer} "
            f"({len(assignments)} assigned slot(s), cmd 4)."
        )

    def write_macro_data(self, macro_id: int, events: list[dict]) -> str:
        """Send one macro's byte stream (cmd 0x05) once."""
        if not self.hidraw:
            raise BackendError(self.install_hint())
        report = self._build_macro_data_report(macro_id, events)
        self._send_feature_report(report)
        return (
            f"Sent macro M{macro_id + 1} ({len(events)} step(s), cmd 5) — "
            "stored on this computer; this keyboard cannot report macros back."
        )

    def change_profile(self, profile: int, layer: int) -> str:
        """Switch the active profile/layer: cmd 0x01 ChangeProfile.

        Exactly four meaningful bytes — [0]=report ID, [1]=1, [2]=profile,
        [3]=layer (CORE sends profile/layer index + 1; this API is 1-based).
        The firmware ignores anything past [3], so no payload is packed. Sent
        once, then the mirror is re-read to confirm the device switched.
        """
        if not self.hidraw:
            raise BackendError(self.install_hint())
        report = self._build_change_profile_report(profile, layer)
        self._send_feature_report(report)
        self._sync_active_profile_layer()
        if (self.profile, self.layer) != (int(profile), int(layer)):
            raise BackendError(
                "ChangeProfile verification FAILED: the mirror still reports profile "
                f"{self.profile} / layer {self.layer}, not {profile} / {layer}. The switch is "
                "UNCONFIRMED — do not chain more writes."
            )
        return f"Switched to profile {profile} layer {layer} (cmd 1 ChangeProfile); read-back confirmed."

    @staticmethod
    def _default_keymap_table() -> bytes:
        """Factory keymap table shipped inside the package (app never depends
        on remap/ at runtime)."""
        return StockGmmkProBackend._read_default_data("default_keymap.bin", "factory keymap table")

    @staticmethod
    def default_keymap_table() -> bytes:
        """Public packaged factory codes table (120 bytes, 0xFF = unassigned).

        When the device mirror answers with the blank shadow buffer
        (KEYMAP_BLANK_TABLE — factory-reset state, remap/PROTOCOL_DECODE.md
        G6 verdict) the GUI displays/exposes THIS packaged default instead,
        labelled honestly as the factory-default source.
        """
        return StockGmmkProBackend._read_default_data("default_keymap.bin", "factory keymap table")

    @staticmethod
    def _default_types() -> bytes:
        """Vendor Buttoninfo_Default per-key TYPES shipped inside the package
        (remap/PROTOCOL_DECODE.md: 0x01 x77 normal, 0x02 x7, 0x03 x1 consumer,
        0xFF x35 unused)."""
        return StockGmmkProBackend._read_default_data("default_types.bin", "factory key types array")

    @staticmethod
    def _read_default_data(filename: str, what: str) -> bytes:
        path = Path(__file__).resolve().parent / "data" / filename
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise BackendError(f"Shipped {what} is missing or unreadable: {exc}") from exc
        if len(data) != KEYMAP_SLOTS:
            raise BackendError(f"Shipped {what} must be {KEYMAP_SLOTS} bytes, found {len(data)}.")
        return data

    @staticmethod
    def _matrix_bytes(name: str, data: object, length: int = KEYMAP_SLOTS) -> bytes:
        """Coerce a codes/types sequence to exactly ``length`` bytes."""
        try:
            raw = bytes(data)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise BackendError(
                f"{name} must be a sequence of {length} byte values 0x00-0xFF, got {data!r}."
            ) from exc
        if len(raw) != length:
            raise BackendError(f"{name} must be exactly {length} bytes, got {len(raw)}.")
        return raw

    def _codes_table(self, mirror: bytes | bytearray) -> bytes:
        """The 120-byte codes table of a config mirror, echo-shape agnostic."""
        offset = locate_codes_table(mirror)
        table = bytes(mirror[offset : offset + KEYMAP_SLOTS])
        if len(table) != KEYMAP_SLOTS:
            raise BackendError(
                f"Config mirror is too short to hold the {KEYMAP_SLOTS}-byte codes table "
                f"at offset 0x{offset:02X} (buffer {len(mirror)} bytes)."
            )
        return table

    def _get_config_mirror(self, profile: int, layer: int) -> bytearray:
        """GET report 7 (via the proven cmd-129 exchange): raw mirror response.

        Device presence + 1-3 selector validation only — no interpretation.
        """
        if not self.hidraw:
            raise BackendError(self.install_hint())
        for name, value in (("profile", profile), ("layer", layer)):
            if not 1 <= int(value) <= 3:
                raise BackendError(f"{name.capitalize()} must be 1-3, got {value!r}.")
        request = self._make_request(CMD_GET_CONFIG)
        request[2] = profile
        request[3] = layer
        return self._exchange_feature_report(request)

    def _read_config_mirror(self, profile: int, layer: int) -> bytearray:
        """Strict mirror read (read path): a parsable active pair is required.

        The "device response carries no parsable profile/layer" BackendError
        raised on a fully blank answer IS the blank-shadow signature that
        callers (app.py /api/keystate) detect and degrade on.
        """
        response = self._get_config_mirror(profile, layer)
        parsed = self._parse_profile_layer_response(response)
        if parsed is None:
            raise BackendError(
                "Config mirror read failed: device response carries no parsable profile/layer."
            )
        if parsed != (int(profile), int(layer)):
            raise BackendError(
                "The config mirror only exposes the ACTIVE profile/layer (currently "
                f"{parsed[0]} / {parsed[1]}); profile {profile} layer {layer} is not active — "
                "switch profiles on the device first."
            )
        return response

    def _read_config_mirror_shadow_tolerant(self, profile: int, layer: int) -> bytearray:
        """Keymap-writer pre-read guard (G8): a blank mirror is legal, not a refusal.

        Live-device evidence (G8_SHADOW_PLAN.md, GMMK Pro WB 320F:5092): the
        GET mirror answers ALL ZERO — header included — even immediately after
        a successful cmd 0x01 bind, so the strict guard in
        _read_config_mirror() made every keymap writer refuse on the very
        board whose writes physically land. Semantics: a parsable pair keeps
        the OLD guard (refuse when the device reports a different active
        pair); a blank mirror (parse → None) is the shadow-only state — the
        target profile/layer is taken as given and nothing is refused.
        """
        response = self._get_config_mirror(profile, layer)
        parsed = self._parse_profile_layer_response(response)
        if parsed is None:
            return response  # shadow buffer: no active-pair information, no refusal
        if parsed != (int(profile), int(layer)):
            raise BackendError(
                "The config mirror only exposes the ACTIVE profile/layer (currently "
                f"{parsed[0]} / {parsed[1]}); profile {profile} layer {layer} is not active — "
                "switch profiles on the device first."
            )
        return response

    def _build_key_matrix_report(
        self,
        profile: int,
        layer: int,
        codes: bytes,
        types: bytes | None = None,
    ) -> bytearray:
        """cmd 0x03 KeyMatrix report — the ONLY keymap-write encoding.

        Byte-exact against remap/PROTOCOL_DECODE.md (SendButtonMatrix2Device):
        ``[0]=0x07`` report id, ``[1]=0x03`` command, ``[2]=profile``,
        ``[3]=layer`` (CORE's 0-based index + 1 == our 1-based value), per-key
        TYPES at ``[8..127]``, CODES at ``[128..247]``, tail zero.
        """
        codes_bytes = self._matrix_bytes("Keymap codes", codes)
        types_bytes = (
            self._default_types() if types is None else self._matrix_bytes("Keymap types", types)
        )
        report = bytearray(self.REPORT_LENGTH)
        report[0] = self.REPORT_ID
        report[1] = CMD_KEY_MATRIX
        report[2] = self._selector(profile, "profile")
        report[3] = self._selector(layer, "layer")
        report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS] = types_bytes
        report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS] = codes_bytes
        return report

    def _build_change_profile_report(self, profile: int, layer: int) -> bytearray:
        """cmd 0x01 ChangeProfile report: four selector bytes, nothing else."""
        report = bytearray(self.REPORT_LENGTH)
        report[0] = self.REPORT_ID
        report[1] = CMD_CHANGE_PROFILE
        report[2] = self._selector(profile, "profile")
        report[3] = self._selector(layer, "layer")
        return report

    @staticmethod
    def _selector(value: int, name: str) -> int:
        number = int(value)
        if not 1 <= number <= 3:
            raise BackendError(f"{name.capitalize()} must be 1-3, got {value!r}.")
        return number

    def _write_keymap_matrix(self, codes: bytes, types: bytes, profile: int, layer: int) -> bool:
        """Send the matrix once, then attempt ONE read-back of the mirror.

        Returns True for the SHADOW case, in both of its observed shapes
        (remap/PROTOCOL_DECODE.md G6 verdict + G8_SHADOW_PLAN.md live probe):
        the read-back response is fully blank (parse → None — the wireless
        board's mirror never populates, header included), or the read-back
        codes table is the all-zero blank shadow (KEYMAP_BLANK_TABLE). The
        mirror is push-only: cmd 0x03 writes physically land while it stays
        blank — a blank/unparsable read-back is "read-back cannot verify",
        NOT a mismatch, so it must not raise and must not retry: no second
        write, no extra GET, and callers return the shadow-caveat success
        message. Returns False when the read-back byte-matches the payload.
        Any other difference is a true mismatch and raises.
        """
        payload = self._build_key_matrix_report(profile, layer, codes, types)
        self._send_feature_report(payload)
        verified = self._get_config_mirror(profile, layer)
        if self._parse_profile_layer_response(verified) is None:
            return True  # fully blank mirror: shadow-only device, cannot verify
        actual = self._codes_table(verified)
        if actual == bytes(codes):
            return False
        if actual == KEYMAP_BLANK_TABLE:
            return True
        diffs = [slot for slot in range(KEYMAP_SLOTS) if actual[slot] != codes[slot]]
        first = diffs[0]
        raise BackendError(
            "Keymap write verification FAILED: read-back does not match the payload "
            f"(first difference at slot {first}: mirror 0x{actual[first]:02X}, "
            f"payload 0x{codes[first]:02X}; {len(diffs)} of {KEYMAP_SLOTS} slots differ). "
            "The write is UNCONFIRMED and the device state is unknown — re-read the "
            "keymap before attempting anything else."
        )

    @staticmethod
    def _shadow_write_message(action: str, profile: int, layer: int) -> str:
        """Honest success wording for a write the blank mirror could not verify."""
        return (
            f"Wrote {action} to profile {profile} layer {layer} (cmd 3 KeyMatrix); "
            "device mirror is blank so read-back could not verify — physical key "
            "test is the only confirmation."
        )


    def _effect_supports_color(self, effect: int | None = None) -> bool:
        active_effect = self.state.effect if effect is None else effect
        return active_effect in self.EFFECT_COLOR_GROUPS

    def _apply_current_state(self) -> None:
        if not self.hidraw:
            raise BackendError(self.install_hint())
        self._sync_active_profile_layer()
        properties = bytearray(self.REPORT_LENGTH)
        effect = bytearray(self.REPORT_LENGTH)
        profile_update = bytearray(self.REPORT_LENGTH)
        self._prepare_properties_buffer(properties)
        self._prepare_effect_buffer(effect)
        self._prepare_change_profile_buffer(profile_update)
        self._send_feature_report(properties)
        self._send_feature_report(effect)
        # G19A vendor apply order: properties (0x07) -> effect (0x02) ->
        # per-key layout pages (0x06 x3) -> profile-update. The pages join
        # the flow ONLY while per-key mode is enabled, so the default
        # (disabled) wire stays byte-identical to HEAD.
        if self.per_key["enabled"]:
            self.apply_per_key_layout()
        self._send_feature_report(profile_update)

    def _prepare_properties_buffer(self, report: bytearray) -> None:
        report[0] = self.REPORT_ID
        report[1] = 7
        report[2] = self.profile
        report[3] = self.layer
        report[4] = self.state.effect
        # G19A: Win_Lock. The vendor HARDCODES 0x00 here on this board
        # (GmmkSeries.ts); our toggle defaults UNLOCKED (0), which keeps the
        # byte — and the whole report — identical to HEAD. Board support is
        # pending the physical gate.
        report[5] = 1 if self.win_lock else 0
        report[6] = 1  # PerKey_sw
        # G18 P2 INVERTED switch: side lights ON -> 0, OFF -> 1. Default ON
        # keeps the pre-G18 byte value 0 (P3 byte-identical behavior).
        report[7] = 0 if self.side_lights else 1
        report[8] = round(self.state.brightness * 20 / 100)
        # G18 P2: RAW input-latency ms (2/8/16). Default 16 is the value this
        # byte was hardcoded to before G18 (P3).
        report[10] = self.input_latency_ms

    def _prepare_effect_buffer(self, report: bytearray) -> None:
        red, green, blue = self.color_rgb
        is_multicolor = self.state.effect in self.MULTICOLOR_EFFECTS
        report[0] = self.REPORT_ID
        report[1] = 2
        report[2] = self.state.effect
        report[8] = max(0, min(100, 100 - self.state.speed))
        report[9] = round(self.state.brightness * 20 / 100)
        report[10] = 1 if is_multicolor else 0
        report[12] = 1 if is_multicolor else 0
        report[15] = red
        report[16] = green
        report[17] = blue

    def _prepare_change_profile_buffer(self, report: bytearray) -> None:
        """cmd 1 ChangeProfile: re-assert the active profile/layer (four
        selector bytes). NOT a config write — firmware ignores bytes past [3]
        (remap/PROTOCOL_DECODE.md); CORE sends this last in its connect
        sequence, which is why the RGB setters keep ending with it."""
        report[0] = self.REPORT_ID
        report[1] = CMD_CHANGE_PROFILE
        report[2] = self.profile
        report[3] = self.layer

    def _sync_active_profile_layer(self) -> None:
        response = self._exchange_feature_report(self._make_request(129))
        parsed = self._parse_profile_layer_response(response)
        if parsed is not None:
            self.profile, self.layer = parsed

    def _make_request(self, request_id: int) -> bytearray:
        report = bytearray(self.REPORT_LENGTH)
        report[0] = self.REPORT_ID
        report[1] = request_id
        report[2] = self.profile
        report[3] = self.layer
        return report

    @staticmethod
    def _parse_profile_layer_response(response: bytearray) -> tuple[int, int] | None:
        """Parse (profile, layer) from a GET config-mirror response.

        Two framings exist on this protocol (remap/DISCOVERY.md operator runs
        2-3): the live hidraw read echoes the 0x07 report ID as byte 0 and
        shifts the payload, putting the selectors at [1]/[2]; payload-only
        captures (the probe-era fixture) keep them at [0]/[1]. [0]/[1] is tried
        first — in the echoed framing byte 0 is 0x07, outside the valid 1-3
        selector range, so it can never match by accident — then the echoed
        pair, the legacy [2]/[3] pair (kept for the Phase 0 characterization
        tests) and the historical [8]/[9] candidate. Same rule as
        locate_codes_table(): parse the shape, never hardcode one offset.
        """
        candidates = [
            (response[0], response[1]),
            (response[1], response[2]),
            (response[2], response[3]),
            (response[8], response[9]),
        ]
        for profile, layer in candidates:
            if 1 <= profile <= 3 and 1 <= layer <= 3:
                return profile, layer
        return None

    def _exchange_feature_report(self, report: bytearray) -> bytearray:
        self._send_feature_report(report)
        read_buf = bytearray(self.REPORT_LENGTH)
        read_buf[0] = self.REPORT_ID
        try:
            fd = os.open(self.hidraw, os.O_RDWR)
        except PermissionError as exc:
            raise BackendError(
                f"Permission denied opening {self.hidraw}. Reload the udev rule and replug the keyboard."
            ) from exc
        try:
            fcntl.ioctl(fd, _hid_iocgfeature(len(read_buf)), read_buf, True)
        except OSError as exc:
            raise BackendError(f"Feature report read failed on {self.hidraw.name}: {exc}") from exc
        finally:
            os.close(fd)
        return read_buf

    def _send_feature_report(self, report: bytearray) -> None:
        try:
            fd = os.open(self.hidraw, os.O_RDWR)
        except PermissionError as exc:
            raise BackendError(
                f"Permission denied opening {self.hidraw}. Reload the udev rule and replug the keyboard."
            ) from exc
        try:
            fcntl.ioctl(fd, _hid_iocsfeature(len(report)), report, True)
        except OSError as exc:
            raise BackendError(f"Feature report failed on {self.hidraw.name}: {exc}") from exc
        finally:
            os.close(fd)

    def _detect_stock_interface(self) -> Path | None:
        for hidraw_sys in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
            uevent = hidraw_sys / 'device' / 'uevent'
            descriptor = hidraw_sys / 'device' / 'report_descriptor'
            if not uevent.exists() or not descriptor.exists():
                continue
            metadata = uevent.read_text(encoding='utf-8', errors='ignore')
            if '0000320F' not in metadata or not any(pid in metadata for pid in ('00005044', '00005092')):
                continue
            data = descriptor.read_bytes()
            if data.startswith(bytes.fromhex('06 01 ff 09 01 a1 01 85 07')):
                return Path('/dev') / hidraw_sys.name
        return None


def _ioc(direction: int, type_: int, nr: int, size: int) -> int:
    return (direction << 30) | (type_ << 8) | (nr << 0) | (size << 16)


def _hid_iocsfeature(length: int) -> int:
    return _ioc(3, ord('H'), 0x06, length)


def _hid_iocgfeature(length: int) -> int:
    return _ioc(3, ord('H'), 0x07, length)


def create_backend() -> BaseBackend:
    stock = StockGmmkProBackend()
    if stock.is_available():
        return stock
    return QmkHidBackend()
