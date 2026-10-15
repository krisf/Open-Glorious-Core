"""USB HID keyboard usage-ID names for the key-remapping GUI.

Derivation sources (no invention):

* ``remap/KEYMAP_MAP.md`` — the factory slot map decoded from the captured
  device dump (fixture ``tests/fixtures/mirror_profile11.bin``); its non-FF
  factory bytes are the ground truth of what this board ships.
* ``src/open_glorious_core/data/default_keymap.bin`` / ``default_types.bin``
  — the packaged factory codes/types tables (Buttoninfo_Default cross-check,
  remap/PROTOCOL_DECODE.md).
* USB HID Usage Tables (Device Class Definitions for HID v1.11, section 10,
  Keyboard/Keypad Usage Page 0x07) — the upstream standard for every name
  from 0x04 to 0x99. Bytes 0x9A–0xDF are *reserved* in that table and get no
  name here.

Vendor-specific codes (documented in remap/KEYMAP_MAP.md "Notes on the three
unlabeled raw bytes" / row 6 — each one carries a note there):

* ``0xA0`` — Fn. Row-6 slot 109 in the factory dump; not a HID usage (0xA0 is
  reserved in the HID table); the firmware's own Fn key. Remapping it is a
  vendor-internal bind — the UI exposes it under the "vendor" group only
  because it is a real, observed factory code.
* ``0x02`` — the knob/mute raw byte. Row-1 slot 14 (knob position); KEYMAP_MAP:
  "not a standard HID usage — vendor-internal… Do not remap without verifying
  physically." Its factory type byte is 0x03 (consumer), the knob/mute path
  (PROTOCOL_DECODE.md Buttoninfo_Default).

Media/consumer keys: the type byte 0x03 row IS supported on this board (the
factory knob/mute uses it), so the media codes that are documented *keyboard
page* usage IDs ship below with TARGET_TYPE 0x03: Mute 0x7F, Volume Up 0x80,
Volume Down 0x81. Play/pause, brightness up/down and sleep are Consumer-page
(0x0C) usages whose single-byte encoding on this firmware is NOT documented in
this repo — TODO operator verify (live keypress test) before adding them; they
are deliberately absent from the shipped map.

This module imports nothing from the project: backend.py must stay importable
from here and from anywhere else with no cycles.
"""
from __future__ import annotations

from typing import Literal

# 0xFF = unassigned slot (backend.KEYMAP_UNASSIGNED; restated locally to keep
# this module import-free of the project).
UNASSIGNED_CODE = 0xFF

# Standard USB HID Keyboard/Keypad (page 0x07) usage IDs with upstream names.
# 0x32 / 0x64 are the ISO Non-US usages; remap/KEYMAP_MAP.md notes slots 72 and
# 92 carry those raw bytes on this ANSI unit with no keycap — the NAMES here
# are the standard usage names, the "leave alone" advice lives in KEYMAP_MAP.
HID_CODE_NAMES: dict[int, str] = {
    0x04: "A",
    0x05: "B",
    0x06: "C",
    0x07: "D",
    0x08: "E",
    0x09: "F",
    0x0A: "G",
    0x0B: "H",
    0x0C: "I",
    0x0D: "J",
    0x0E: "K",
    0x0F: "L",
    0x10: "M",
    0x11: "N",
    0x12: "O",
    0x13: "P",
    0x14: "Q",
    0x15: "R",
    0x16: "S",
    0x17: "T",
    0x18: "U",
    0x19: "V",
    0x1A: "W",
    0x1B: "X",
    0x1C: "Y",
    0x1D: "Z",
    0x1E: "1",
    0x1F: "2",
    0x20: "3",
    0x21: "4",
    0x22: "5",
    0x23: "6",
    0x24: "7",
    0x25: "8",
    0x26: "9",
    0x27: "0",
    0x28: "Enter",
    0x29: "Escape",
    0x2A: "Backspace",
    0x2B: "Tab",
    0x2C: "Space",
    0x2D: "-",
    0x2E: "=",
    0x2F: "[",
    0x30: "]",
    0x31: "\\",
    0x32: "# ~ (Non-US)",
    0x33: ";",
    0x34: "'",
    0x35: "`",
    0x36: ",",
    0x37: ".",
    0x38: "/",
    0x39: "Caps Lock",
    0x3A: "F1",
    0x3B: "F2",
    0x3C: "F3",
    0x3D: "F4",
    0x3E: "F5",
    0x3F: "F6",
    0x40: "F7",
    0x41: "F8",
    0x42: "F9",
    0x43: "F10",
    0x44: "F11",
    0x45: "F12",
    0x46: "Print Screen",
    0x47: "Scroll Lock",
    0x48: "Pause",
    0x49: "Insert",
    0x4A: "Home",
    0x4B: "Page Up",
    0x4C: "Delete",
    0x4D: "End",
    0x4E: "Page Down",
    0x4F: "Right Arrow",
    0x50: "Left Arrow",
    0x51: "Down Arrow",
    0x52: "Up Arrow",
    0x53: "Num Lock",
    0x54: "Keypad /",
    0x55: "Keypad *",
    0x56: "Keypad -",
    0x57: "Keypad +",
    0x58: "Keypad Enter",
    0x59: "Keypad 1",
    0x5A: "Keypad 2",
    0x5B: "Keypad 3",
    0x5C: "Keypad 4",
    0x5D: "Keypad 5",
    0x5E: "Keypad 6",
    0x5F: "Keypad 7",
    0x60: "Keypad 8",
    0x61: "Keypad 9",
    0x62: "Keypad 0",
    0x63: "Keypad .",
    0x64: "\\ | (Non-US)",
    0x65: "Application",
    0x66: "Power",
    0x67: "Keypad =",
    0x68: "F13",
    0x69: "F14",
    0x6A: "F15",
    0x6B: "F16",
    0x6C: "F17",
    0x6D: "F18",
    0x6E: "F19",
    0x6F: "F20",
    0x70: "F21",
    0x71: "F22",
    0x72: "F23",
    0x73: "F24",
    0x74: "Execute",
    0x75: "Help",
    0x76: "Menu",
    0x77: "Select",
    0x78: "Stop",
    0x79: "Again",
    0x7A: "Undo",
    0x7B: "Cut",
    0x7C: "Copy",
    0x7D: "Paste",
    0x7E: "Find",
    0x7F: "Mute",
    0x80: "Volume Up",
    0x81: "Volume Down",
    0x82: "Locking Caps Lock",
    0x83: "Locking Num Lock",
    0x84: "Locking Scroll Lock",
    0x85: "Keypad Comma",
    0x86: "Keypad Equal Sign",
    0x87: "International1",
    0x88: "International2",
    0x89: "International3",
    0x8A: "International4",
    0x8B: "International5",
    0x8C: "International6",
    0x8D: "International7",
    0x8E: "International8",
    0x8F: "International9",
    0x90: "Language1",
    0x91: "Language2",
    0x92: "Language3",
    0x93: "Language4",
    0x94: "Language5",
    0x95: "Language6",
    0x96: "Language7",
    0x97: "Language8",
    0x98: "Language9",
    0x99: "Language10",
}

# Standard modifier usage IDs (USB HID page 0x07, 0xE0–0xE7).
HID_MODIFIER_CODES: dict[int, str] = {
    0xE0: "Left Ctrl",
    0xE1: "Left Shift",
    0xE2: "Left Alt",
    0xE3: "Left GUI",  # the "Win" key (factory row 6: "Left Win")
    0xE4: "Right Ctrl",
    0xE5: "Right Shift",
    0xE6: "Right Alt",
    0xE7: "Right GUI",
}

# Non-standard codes observed in the factory table (see module docstring and
# remap/KEYMAP_MAP.md "Notes on the three unlabeled raw bytes" — every entry
# here is documented there).
VENDOR_CODE_NAMES: dict[int, str] = {
    0xA0: "Fn (vendor)",  # factory slot 109; reserved in the HID usage table
    0x02: "Knob/Mute (vendor)",  # factory slot 14; KEYMAP_MAP: do not remap blindly
}

# Per-key TYPE byte the firmware needs for a code to work (PROTOCOL_DECODE.md
# "Type mask semantics": 0x01 normal, 0x02 the modifier keycaps, 0x03
# consumer). The UI filters on this; writes default to the packaged
# data/default_types.bin and only deviate where this table says so.
TARGET_TYPE_NORMAL = 0x01
TARGET_TYPE_MODIFIER = 0x02
TARGET_TYPE_CONSUMER = 0x03
TARGET_TYPE_BY_CODE: dict[int, int] = {
    0x7F: TARGET_TYPE_CONSUMER,  # Mute — factory knob/mute is type 0x03
    0x80: TARGET_TYPE_CONSUMER,  # Volume Up
    0x81: TARGET_TYPE_CONSUMER,  # Volume Down
    0x02: TARGET_TYPE_CONSUMER,  # vendor knob/mute raw byte
}

# UI dropdown groups (Deliverable 4: alpha / numeric / F-keys / modifiers /
# nav / media / vendor + keypad / editing / system / punct / intl).
CODE_GROUPS: dict[int, str] = {}


def _fill_group(group: str, codes: range | list[int] | tuple[int, ...]) -> None:
    for code in codes:
        CODE_GROUPS[code] = group


_fill_group("alpha", range(0x04, 0x1E))
_fill_group("numeric", range(0x1E, 0x28))
_fill_group("editing", [0x28, 0x2A, 0x2B, 0x2C])
_fill_group("punct", [0x2D, 0x2E, 0x2F, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x64])
_fill_group("system", [0x29, 0x39, 0x53, 0x65, 0x66, 0x82, 0x83, 0x84])
_fill_group("fkeys", list(range(0x3A, 0x46)) + list(range(0x68, 0x74)))
_fill_group("system", [0x46, 0x47, 0x48])
_fill_group("nav", list(range(0x49, 0x53)))
_fill_group("keypad", list(range(0x54, 0x64)) + [0x67, 0x85, 0x86])
_fill_group("editing", range(0x74, 0x7F))
_fill_group("media", [0x7F, 0x80, 0x81])
_fill_group("intl", range(0x87, 0x9A))
_fill_group("modifiers", list(HID_MODIFIER_CODES))
_fill_group("vendor", list(VENDOR_CODE_NAMES))


# Search aliases (G9 FIX C): bounded, domain-derived strings — NOT new usage
# names (code_to_name stays the naming source of truth). Matching happens on
# lowercased, non-alphanumeric-stripped tokens (see static/app.js pure search
# helpers), so every published alias is exactly that: lowercase, alphanumeric.
# Plain single digits are deliberately NOT aliased anywhere: the digits-row
# names "1"–"0" already prefix-match those queries.
# Alias groups (documented, one per code):
#   navigation  — standard abbreviations (prtsc, ins, pgup, pgdn, del, esc…)
#   system      — caps / numlock / menu shorthands
#   keypad      — the obvious short families num*/numpad*/kp*/pad*/p* for the
#                 ten keypad digits, word forms for the keypad operators
#   media       — mute / volup / voldown words
#   modifiers   — lctrl/ctrl, lshift/shift, win/super/meta/gui/cmd family
#   editing     — return / spacebar / erase-back
#   vendor      — Fn key words
#   unassigned  — the 0xFF "clear the key" words
CODE_ALIASES: dict[int, tuple[str, ...]] = {
    # navigation
    0x29: ("esc",),
    0x46: ("prtsc", "prtscn", "printscreen"),
    0x47: ("scrlock", "scrolllock"),
    0x48: ("brk", "pausebreak"),
    0x49: ("ins",),
    0x4a: ("homekey",),
    0x4b: ("pgup", "pageup"),
    0x4c: ("del", "deleteforward"),
    0x4d: ("endkey",),
    0x4e: ("pgdn", "pagedn", "pagedown"),
    0x4f: ("right", "arrowright", "rightarrow"),
    0x50: ("left", "arrowleft", "leftarrow"),
    0x51: ("down", "arrowdown", "downarrow"),
    0x52: ("up", "arrowup", "uparrow"),
    # system
    0x39: ("caps",),
    0x53: ("numlock",),
    0x65: ("menu", "appmenu", "contextmenu"),
    # keypad
    0x54: ("kpdiv", "numdiv", "paddiv", "kpdivide"),
    0x55: ("kpmul", "nummul", "padmul", "kpstar", "kpmultiply"),
    0x56: ("kpminus", "kpmin", "kpsub"),
    0x57: ("kpplus", "kpadd"),
    0x58: ("kpenter", "numenter"),
    0x59: ("num1", "numpad1", "kp1", "p1"),
    0x5a: ("num2", "numpad2", "kp2", "p2"),
    0x5b: ("num3", "numpad3", "kp3", "p3"),
    0x5c: ("num4", "numpad4", "kp4", "p4"),
    0x5d: ("num5", "numpad5", "kp5", "p5"),
    0x5e: ("num6", "numpad6", "kp6", "p6"),
    0x5f: ("num7", "numpad7", "kp7", "p7"),
    0x60: ("num8", "numpad8", "kp8", "p8"),
    0x61: ("num9", "numpad9", "kp9", "p9"),
    0x62: ("num0", "numpad0", "kp0", "p0"),
    0x63: ("num00", "numpad00", "kp00"),
    0x67: ("kpequal", "kpequals"),
    0x85: ("kpcomma",),
    0x86: ("kpequalsign",),
    # media
    0x7f: ("mute", "volumemute", "audiomute"),
    0x80: ("volup", "volumeup", "louder"),
    0x81: ("voldown", "volumedown", "volminus"),
    # modifiers
    0xe0: ("ctrl", "lctrl", "leftctrl", "control"),
    0xe1: ("shift", "lshift", "leftshift"),
    0xe2: ("alt", "lalt", "leftalt", "option"),
    0xe3: ("win", "lwin", "leftwin", "super", "meta", "gui", "cmd", "command"),
    0xe4: ("rctrl", "rightctrl"),
    0xe5: ("rshift", "rightshift"),
    0xe6: ("ralt", "rightalt", "altgr"),
    0xe7: ("rwin", "rightwin", "rgui", "rmeta"),
    # editing
    0x28: ("return",),
    0x2a: ("delback", "eraseback"),
    0x2c: ("spacebar",),
    # vendor
    0xa0: ("fnkey", "function"),
    # unassigned (0xFF)
    0xff: ("none", "blank", "clear", "erase"),
}


def code_aliases(code: int) -> tuple[str, ...]:
    """Bounded search aliases for the remap dropdown (G9 FIX C).

    Domain-derived strings only; every published alias is already in
    normalized form (lowercase, alphanumerics only) so the UI can match with
    a plain startswith after normalizing the query the same way."""
    return CODE_ALIASES.get(code, ())


def code_to_name(
    code: int,
    *,
    fall_behaviour: Literal["byte", "error"] = "byte",
) -> str:
    """Human name for one keymap byte, e.g. ``"A (0x04)"``.

    * ``0xFF`` -> ``"Unassigned (0xFF)"`` (KEYMAP_UNASSIGNED slot).
    * Any byte 0x00–0xFE without a documented name -> ``"Reserved 0xXX"``
      in ``fall_behaviour="byte"`` mode (the honest GUI rendering of a
      reserved/undocumented byte).
    * ``fall_behaviour="error"`` raises ``KeyError`` for every byte without
      a documented name — that mode is for callers that treat an unmapped
      byte as a programming error.
    * A structurally invalid value (not an int in 0x00–0xFF, e.g. 0x100)
      raises ``KeyError`` in BOTH modes: there is no byte notation for it.
    """
    if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code <= 0xFF:
        raise KeyError(f"Not a keymap byte (0x00-0xFF): {code!r}")
    if code == UNASSIGNED_CODE:
        return "Unassigned (0xFF)"
    name = HID_CODE_NAMES.get(code)
    if name is None:
        name = HID_MODIFIER_CODES.get(code)
    if name is None:
        name = VENDOR_CODE_NAMES.get(code)
    if name is None:
        if fall_behaviour == "error":
            raise KeyError(f"No documented usage name for byte 0x{code:02X}")
        return f"Reserved 0x{code:02X}"
    return f"{name} (0x{code:02X})"


def code_group(code: int) -> str:
    """Dropdown group for a code: alpha / numeric / fkeys / modifiers / nav /
    media / vendor / keypad / editing / system / punct / intl / other."""
    return CODE_GROUPS.get(code, "other")


def target_type(code: int) -> int:
    """Firmware type byte needed for ``code`` (0x01/0x02/0x03, see
    TARGET_TYPE_BY_CODE); modifiers default to 0x02, everything else 0x01."""
    explicit = TARGET_TYPE_BY_CODE.get(code)
    if explicit is not None:
        return explicit
    if code in HID_MODIFIER_CODES:
        return TARGET_TYPE_MODIFIER
    return TARGET_TYPE_NORMAL


def all_assignable_codes() -> list[int]:
    """Every code the GUI may offer: documented HID names, modifiers, vendor
    specials, plus 0xFF (Unassign)."""
    codes = set(HID_CODE_NAMES) | set(HID_MODIFIER_CODES) | set(VENDOR_CODE_NAMES)
    codes.add(UNASSIGNED_CODE)
    return sorted(codes)
