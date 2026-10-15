from __future__ import annotations

import json
import sys
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from . import lastgood
from .backend import (
    KEYMAP_SLOTS,
    KEYMAP_SLOT_LABELS,
    KEYMAP_UNASSIGNED,
    BackendError,
    KeymapEntry,
    create_backend,
)
from .device import HidDevice, detect_gmmk_pro
from .keycodes import (
    all_assignable_codes,
    code_aliases,
    code_group,
    code_to_name,
    target_type,
)

STATIC_DIR = Path(__file__).with_name("static")
ICON_PATH = STATIC_DIR / "assets" / "open-glorious-core-icon.ico"
DATA_DIR = Path(__file__).with_name("data")
HOST = "127.0.0.1"

# Keymap panel constants. The types table ships as the packaged factory
# default; G15: writes NEVER send it verbatim — the controller pairs every
# full-matrix write with EFFECTIVE types (build_effective_types below),
# because the WB5092 firmware honors the TYPE byte as cell activation:
# a 0xFF-typed cell ignores its code byte entirely, so an assigned cell with
# the vendor 0xFF default type is a cell that does nothing (operator proof,
# G15_TYPES_OVERLAY_PLAN.md: the GUI write codes[58]=0x54 with implicit 0xFF
# did nothing; the manual recovery write with types[58]=0x01 landed — the
# types overlay mechanism, E2E-pinned; the caps→NumpadDivide cell of that
# incident is slot 60 per the G13 sweep, G17).
KEYMAP_TYPE_LABELS = {0x01: "normal", 0x02: "modifier", 0x03: "consumer", 0xFF: "unused"}
KEYMAP_TYPE_NORMAL = 0x01
KEYMAP_TYPE_MODIFIER = 0x02
KEYMAP_TYPE_CONSUMER = 0x03
# Types a write must never overwrite: the normal keys, the vendor modifier
# set (0x02) and the knob/mute consumer typing (0x03) — G15 fix spec rule 3.
KEYMAP_TYPES_PROTECTED = frozenset(
    {KEYMAP_TYPE_NORMAL, KEYMAP_TYPE_MODIFIER, KEYMAP_TYPE_CONSUMER}
)
# HID usage range of the modifier codes (Keyboard LeftControl … Right GUI).
KEYMAP_MODIFIER_CODE_RANGE = (0xE0, 0xE7)


def build_effective_types(codes: bytes, default_types: bytes) -> bytes:
    """G15 fix spec 1: the per-key TYPE bytes that make the firmware activate
    the cells in ``codes``.

    Start from the packaged vendor defaults (data/default_types.bin), then:

    * slots whose default type is 0x01/0x02/0x03 are NEVER touched — that
      protects the knob's 0x03 consumer typing and the vendor modifier set;
    * any slot the vendor leaves 0xFF ("unassigned") whose effective code is
      non-0xFF becomes 0x01 (normal key) — this is what activates every
      GUI-assigned spare (the factory CapsLock cell 60 is already type 0x01,
      so a plain factory vector needs no activation at all);
    * a slot assigned a modifier code (0xE0–0xE7) becomes 0x02, matching the
      vendor modifier typing;
    * an unassigned slot (code 0xFF) keeps the vendor type byte.

    Pure function: no IO, no device (headless-testable, E2E-pinned).
    """
    effective = bytearray(default_types)
    for slot, code in enumerate(codes[: len(default_types)]):
        if default_types[slot] in KEYMAP_TYPES_PROTECTED:
            continue
        if code == KEYMAP_UNASSIGNED:
            continue
        effective[slot] = (
            KEYMAP_TYPE_MODIFIER
            if KEYMAP_MODIFIER_CODE_RANGE[0] <= code <= KEYMAP_MODIFIER_CODE_RANGE[1]
            else KEYMAP_TYPE_NORMAL
        )
    return bytes(effective)
KEYMAP_PROFILES = (1, 2, 3)
KEYMAP_LAYERS = (1, 2, 3)
# Exact banner text the UI must show when the mirror is the blank shadow
# buffer (G7 plan / PROTOCOL_DECODE.md G6: GUI shows factory defaults instead).
KEYMAP_SHADOW_BANNER = "Device buffer not initialized — showing factory default"
# Exact tooltip wording for non-active profile/layer cards.
KEYMAP_SWITCH_HINT = "switch-profile first"
# G8 live-device signature (GMMK Pro WB 320F:5092, operator gate 10-15): the
# GET mirror is fully blank — even right after a bind — so the strict backend
# read raises with this text. It is the parse-None signature /api/keystate
# degrades on (factory defaults, ok:true), never a hard panel failure.
KEYMAP_BLANK_MIRROR_SIGNATURE = "no parsable profile/layer"
# Slots of the 120-slot matrix that carry NO physical keycap on this board
# (G9 FIX A, single source of truth for the UI; remap/KEYMAP_MAP.md evidence):
#   * 14, 72, 92 — the three raw-byte matrix artifacts ("Notes on the three
#     unlabeled raw bytes", KEYMAP_MAP.md lines 32-42: knob area / ISO slot /
#     phantom Menu slot; "no keycap here", "Leave alone"; row anatomy lines
#     21, 24-25).
#   * 103, 104, 106, 107, 111 — the five `··` spacer positions of the bottom
#     row (row anatomy line 26: `LCtrl LWin LAlt ·· Space ·· RAlt Fn RCtrl ·
#     ← ↓ →`; row-6 table lines 178-186 mark them `*(unassigned spare)*`
#     with factory code 0xFF — verified against the packaged fixture
#     data/default_keymap.bin: bytes 103/104/106/107/111 == 0xFF).
#   * 55, 56, 57, 58, 59 — the head of the row-4 USB block, BEFORE Caps Lock:
#     factory 0xFF cells that carry no keycap (KEYMAP_MAP.md lines 115-119;
#     data/default_keymap.bin[55..59] are 0xFF). G17 RETRACTS the G11 claim
#     that slot 58 is this board's CapsLock cell: the G13 cell sweep wrote F13
#     (0x68) to 58 and F14 (0x69) to 60, and a Caps Lock press emitted
#     `00 00 69` twice on the OS listener with nothing from 58 — Caps Lock
#     scans at slot 60 (the vendor table's own CapsLock cell), so 58 is an
#     ordinary dead spare of the head-of-block group and is hidden exactly
#     like its neighbours. Slot 60 is a REAL keycap now (grid head of row 4).
NON_KEYCAP_SLOTS = frozenset({14, 55, 56, 57, 58, 59, 72, 92, 103, 104, 106, 107, 111})
KEYMAP_BLANK_MIRROR_MESSAGE = (
    "Config mirror is blank (shadow buffer never populates on this wireless "
    "board post-factory-reset); showing packaged factory defaults. Writes "
    "still land and physical key test is the only verification."
)
# G16 (plan item 3): after EVERY successful apply-keymap/reset-keymap the
# drawer banner states which profile/layer was written and how the operator
# resolves a profile drift himself. The GUI's profile/layer is a software
# counter (the GET mirror is blank on this wireless board — no readback), so
# FN+CTRL+↑ / re-pair / sleep can move the real active profile out from under
# it, and a matrix write aimed at a stale pair is indistinguishable from no
# write at all (the 13:07 NO CHANGE, G16_BIND_FIRST_PLAN.md). Exact operator-
# facing sentence — E2E-pinned verbatim.
KEYMAP_BIND_WRITE_BANNER = (
    "Wrote profile {profile} layer {layer} — if your key still misbehaves, "
    "the board is on a different profile: cycle profiles with FN+CTRL+↑ "
    "(or press Write again; every write now targets this exact profile)."
)

# --- G12: automated remap verification + self-healing revert -----------------
# Physics constraint (G12_AUTOVERIFY_PLAN.md): a remap's effect is observable
# ONLY through a physical keystroke. What IS automated here: the verdict is
# read from OS keyboard-event codes (this endpoint, not the operator's eyes),
# and failure is REPAIRED (auto-revert to the last-good matrix). The operator
# spends one optional keystroke; if they spend none, the app reverts.
VERIFY_TIMEOUT_SECONDS = 60.0
# G15 item 4: the 12:51 timeout was UNFOCUSED input (the operator typed in
# other windows, nothing reached the app). Operator guidance beat: the exact
# focus sentence is prepended to the arm banner.
VERIFY_ARM_BANNER = (
    "Click this app window, then press the key. "
    "press your remapped key once (60 s)"
)
VERIFY_MATCH_TEMPLATE = "VERIFIED — {key} now emits {name}"
VERIFY_REVERT_BANNER = "no input seen — auto-reverted to last good config"
VERIFY_SEED_NO_REVERT_BANNER = (
    "no input seen — NOT auto-reverted: this profile/layer has no "
    "operator-confirmed config yet (the last-good seed is only the unchanged "
    "factory table)"
)
VERIFY_NO_REVERT_BANNER = (
    "no input seen — NOT auto-reverted: this profile/layer has no "
    "operator-confirmed config yet"
)
VERIFY_REVERT_FAILED_TEMPLATE = (
    "no input seen — AUTO-REVERT FAILED: {error} (device state unverified; "
    "manual recovery required)"
)
# Kept in the app log (controller.app_log, surfaced in every verify payload).
VERIFY_LOG_LIMIT = 200

# Explicit HID usage -> KeyboardEvent.code variants (G12 plan item 3). The
# matcher maps the EXPECTED HID code of a remap to every code value the OS can
# report for it — including the NumLock-dependent keypad variants, the exact
# class the capslock incident was made of (0x59 with NumLock OFF emits End,
# NOT Numpad1). Codes with no browser keydown identity (vendor raw bytes, Fn,
# unassigned, system/editing usages) map to () : the key emits nothing the OS
# can observe, so the matcher can never match and the self-heal path decides.
KEY_EVENT_CODE_VARIANTS: dict[int, tuple[str, ...]] = {
    0x02: (),  # knob/mute vendor raw — no browser keydown identity
    0xA0: (),  # Fn — firmware-internal, emits no OS keydown
    0xFF: (),  # Unassigned — the key emits NOTHING (timeout is the answer)
    0x04: ("KeyA",), 0x05: ("KeyB",), 0x06: ("KeyC",), 0x07: ("KeyD",),
    0x08: ("KeyE",), 0x09: ("KeyF",), 0x0A: ("KeyG",), 0x0B: ("KeyH",),
    0x0C: ("KeyI",), 0x0D: ("KeyJ",), 0x0E: ("KeyK",), 0x0F: ("KeyL",),
    0x10: ("KeyM",), 0x11: ("KeyN",), 0x12: ("KeyO",), 0x13: ("KeyP",),
    0x14: ("KeyQ",), 0x15: ("KeyR",), 0x16: ("KeyS",), 0x17: ("KeyT",),
    0x18: ("KeyU",), 0x19: ("KeyV",), 0x1A: ("KeyW",), 0x1B: ("KeyX",),
    0x1C: ("KeyY",), 0x1D: ("KeyZ",),
    0x1E: ("Digit1",), 0x1F: ("Digit2",), 0x20: ("Digit3",), 0x21: ("Digit4",),
    0x22: ("Digit5",), 0x23: ("Digit6",), 0x24: ("Digit7",), 0x25: ("Digit8",),
    0x26: ("Digit9",), 0x27: ("Digit0",),
    0x28: ("Enter",), 0x29: ("Escape",), 0x2A: ("Backspace",), 0x2B: ("Tab",),
    0x2C: ("Space",), 0x2D: ("Minus",), 0x2E: ("Equal",),
    0x2F: ("BracketLeft",), 0x30: ("BracketRight",), 0x31: ("Backslash",),
    0x32: ("IntlBackslash",), 0x33: ("Semicolon",), 0x34: ("Quote",),
    0x35: ("Backquote",), 0x36: ("Comma",), 0x37: ("Period",),
    0x38: ("Slash",),
    0x39: ("CapsLock",),  # CapsLock's own code (slot 60, G13 sweep)
    0x3A: ("F1",), 0x3B: ("F2",), 0x3C: ("F3",), 0x3D: ("F4",),
    0x3E: ("F5",), 0x3F: ("F6",), 0x40: ("F7",), 0x41: ("F8",),
    0x42: ("F9",), 0x43: ("F10",), 0x44: ("F11",), 0x45: ("F12",),
    0x46: ("PrintScreen",), 0x47: ("ScrollLock",), 0x48: ("Pause",),
    0x49: ("Insert",), 0x4A: ("Home",), 0x4B: ("PageUp",), 0x4C: ("Delete",),
    0x4D: ("End",), 0x4E: ("PageDown",), 0x4F: ("RightArrow",),
    0x50: ("LeftArrow",), 0x51: ("DownArrow",), 0x52: ("UpArrow",),
    0x53: ("NumLock",), 0x54: ("NumpadDivide",), 0x55: ("NumpadMultiply",),
    0x56: ("NumpadSubtract",), 0x57: ("NumpadAdd",), 0x58: ("NumpadEnter",),
    # NumLock-dependent keypad variants (G12 plan item 3, the capslock class):
    # with NumLock OFF the keypad cells emit their navigation codes.
    0x59: ("Numpad1", "End"),
    0x5A: ("Numpad2", "Down"),
    0x5B: ("Numpad3", "PageDown"),
    0x5C: ("Numpad4", "Left"),
    0x5D: ("Numpad5", "Clear"),
    0x5E: ("Numpad6", "Right"),
    0x5F: ("Numpad7", "Home"),
    0x60: ("Numpad8", "Up"),
    0x61: ("Numpad9", "PageUp"),
    0x62: ("Numpad0", "Insert"),
    0x63: ("NumpadDecimal", "Delete"),
    0x64: ("Backslash", "IntlBackslash"),  # non-US \| , layout-dependent
    0x65: ("ContextMenu",),
    0x66: (),  # Power — system-level, never a browser keydown
    0x67: ("NumpadEqual",),
    0x68: ("F13",), 0x69: ("F14",), 0x6A: ("F15",), 0x6B: ("F16",),
    0x6C: ("F17",), 0x6D: ("F18",), 0x6E: ("F19",), 0x6F: ("F20",),
    0x70: ("F21",), 0x71: ("F22",), 0x72: ("F23",), 0x73: ("F24",),
    0x74: ("Execute",), 0x75: ("Help",), 0x76: ("ContextMenu",),
    0x77: (), 0x78: ("AudioVolumeStop",), 0x79: ("Again",), 0x7A: ("Undo",),
    0x7B: ("Cut",), 0x7C: ("Copy",), 0x7D: ("Paste",), 0x7E: ("Find",),
    0x7F: ("VolumeMute",), 0x80: ("VolumeUp",), 0x81: ("VolumeDown",),
    0x82: ("CapsLock",), 0x83: ("NumLock",), 0x84: ("ScrollLock",),
    0x85: ("NumpadComma",), 0x86: ("NumpadEqual",),
    0x87: ("IntlRo", "International1"), 0x88: ("IntlYen", "International2"),
    0x89: ("International3",), 0x8A: ("International4",),
    0x8B: ("International5",), 0x8C: ("International6",),
    0x8D: ("International7",), 0x8E: ("International8",),
    0x8F: ("International9",),
    0x90: ("Lang1",), 0x91: ("Lang2",), 0x92: ("Lang3",), 0x93: ("Lang4",),
    0x94: ("Lang5",), 0x95: ("Lang6",), 0x96: ("Lang7",), 0x97: ("Lang8",),
    0x98: ("Lang9",), 0x99: ("Lang10",),
    0xE0: ("ControlLeft",), 0xE1: ("ShiftLeft",), 0xE2: ("AltLeft",),
    0xE3: ("MetaLeft",), 0xE4: ("ControlRight",), 0xE5: ("ShiftRight",),
    0xE6: ("AltRight",), 0xE7: ("MetaRight",),
}


def key_event_variants(code: int) -> tuple[str, ...]:
    """Every KeyboardEvent.code the OS can report for an expected HID code."""
    return KEY_EVENT_CODE_VARIANTS.get(int(code), ())


# --- G10 FIX 5: matrix self-test aid (DEFAULT OFF — explicit opt-in only) ---
# The GUI never runs this scan on its own: it fires ONLY on an operator click
# of "Run matrix self-test" (POST /api/action) or the CLI flag
# `--self-test-matrix`. The scan is READ-ONLY by construction — it reads the
# config mirror through the same GET path the panel open uses and calls no
# writer, ever.
MATRIX_SELF_TEST_FLAG = "--self-test-matrix"
MATRIX_SELF_TEST_ACTION = "matrix-self-test"

SLOT_TABLE_JS_PATH = STATIC_DIR / "keymap-slots.js"
SLOT_TABLE_START = "// [KEYMAP-SLOT-TABLE-START]"
SLOT_TABLE_END = "// [KEYMAP-SLOT-TABLE-END]"


def load_slot_table(path: Path = SLOT_TABLE_JS_PATH) -> dict:
    """Parse the strict-JSON slot block shipped in static/keymap-slots.js.

    The JS file is the single source of truth for the visual-row slot
    mapping (transcribed from remap/KEYMAP_MAP.md); parsing it here keeps
    Python-side consumers (the self-test report) on the SAME table the grid
    renders — a second transcription in Python is exactly the drift class
    that produced the slot-58 incident.
    """
    text = path.read_text(encoding="utf-8")
    block = text.split(SLOT_TABLE_START, 1)[1].split(SLOT_TABLE_END, 1)[0]
    payload = block.split("var KEYMAP_SLOT_TABLE = ", 1)[1].rsplit(";", 1)[0]
    return json.loads(payload)


SLOT_TABLE = load_slot_table()

SELF_TEST_CAPS_BOUND = sum(
    1
    for row in SLOT_TABLE["rows"]
    for key in row["keys"]
    if key is not None
)


def build_matrix_self_test_report(
    codes: list[int], factory: bytes, source_label: str, notes: list[str]
) -> list[str]:
    """Scan report: which GUI-labeled keys are alive per the map.

    ``codes`` is the slot->code table to audit (device mirror, or the
    factory table on the blank-mirror degradation). Pure function — the
    headless tests feed it tables directly.
    """
    lines = [
        "MATRIX SELF-TEST — read-only scan of the config mirror (GET only, "
        "ZERO writes).",
        f"Source: {source_label}",
        "G13 sweep verdict (operator-verified 2026-10-15, G17): CapsLock = "
        "slot 60 — the vendor table's OWN CapsLock cell (factory 0x39). The "
        "G11 'empirical slot 58' claim is RETRACTED: F13@58 + F14@60 written, "
        "caps press emitted 00 00 69 twice, nothing from 58.",
    ]
    lines.extend(f"NOTE: {note}" for note in notes)
    lines.append("")
    for row in SLOT_TABLE["rows"]:
        lines.append(f"# {row['name']}")
        for slot, key in zip(row["slots"], row["keys"]):
            if key is None:
                continue  # keycap-less positions: reported in the spares block
            code = int(codes[slot])
            factory_code = int(factory[slot])
            if code == factory_code:
                status = "as mapped"
            elif code == KEYMAP_UNASSIGNED:
                status = f"UNASSIGNED (0xFF) — keycap dead (factory 0x{factory_code:02X})"
            else:
                status = f"DEVIATES (factory 0x{factory_code:02X})"
            lines.append(f"  slot {slot:>3}  {key:<12} code 0x{code:02X}  {status}")
    lines.append("")
    lines.append("Keycap-less slots (never rendered, bound to NO cap):")
    lines.append("  14, 72, 92                 matrix artifacts (KEYMAP_MAP.md 32-42)")
    lines.append("  55, 56, 57, 58, 59         head of row-4 block, factory 0xFF — dead "
                 "spare cells (G13 sweep: 58 emits nothing; the G11 caps@58 claim is "
                 "retracted)")
    lines.append("  103, 104, 106, 107, 111   row-6 spacers, factory 0xFF")
    hidden_no_cap = (55, 56, 57, 58, 59)
    bound_hidden = any(
        slot in hidden_no_cap
        for row in SLOT_TABLE["rows"]
        for slot, key in zip(row["slots"], row["keys"])
        if key is not None
    )
    lines.append(
        f"Caps bound: {SELF_TEST_CAPS_BOUND}; slots 55-59 bound to a "
        f"cap: {'YES — DEFECT' if bound_hidden else 'NO'}"
    )
    lines.append(
        "The mirror scan reports codes, not presses: physical aliveness of a "
        "labeled key is confirmed ONLY by pressing it (see KEYMAP_MAP.md "
        "provenance of the slot map)."
    )
    return lines


def _group_devices(devices: list[HidDevice]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for device in devices:
        key = (device.name, device.vendor_id, device.product_id, device.physical_id)
        if key not in grouped:
            grouped[key] = {
                "name": device.name,
                "vendor_id": device.vendor_id,
                "product_id": device.product_id,
                "physical_id": device.physical_id,
                "hidraws": [],
                "interfaces": [],
            }
        grouped[key]["hidraws"].append(device.hidraw)
        grouped[key]["interfaces"].append(device.interface_id)

    for entry in grouped.values():
        entry["hidraws"] = sorted(entry["hidraws"])
        entry["interfaces"] = sorted(entry["interfaces"])

    return list(grouped.values())


class _VerifyArmed:
    """One armed verification of a written remap (G12 plan item 3).

    ``variants`` is the union of every KeyboardEvent.code the expected HID
    codes can produce (incl. the NumLock-off keypad variants — the exact class
    the capslock incident was made of). The armed object identity doubles as
    the timer token: a cancelled timer firing late sees a mismatch and stops.
    """

    def __init__(
        self,
        slot: int,
        expect: tuple[int, ...],
        variants: frozenset[str],
        profile: int,
        layer: int,
        timeout: float,
    ) -> None:
        self.slot = slot
        self.expect = expect
        self.variants = variants
        self.profile = profile
        self.layer = layer
        self.deadline = time.monotonic() + timeout
        self.timer: threading.Timer | None = None


class OpenGloriousCoreWebController:
    def __init__(self) -> None:
        self.backend = create_backend()
        # One HID conversation at a time: ThreadingHTTPServer hands every
        # request its own thread, and the feature-report interface is a
        # single shared device — never interleave report exchanges.
        self._io_lock = threading.Lock()
        # G12 verify-armed state: the armed matcher + its 60 s auto-revert
        # timer. _verify_state/_verify_banner carry the last verdict so the UI
        # (and the tests) can read the outcome: armed | verified | reverted |
        # no-input-no-revert | revert-failed.
        self._verify_lock = threading.Lock()
        self._verify_armed: _VerifyArmed | None = None
        self._verify_state: str | None = None
        self._verify_banner: str | None = None
        self._verify_timeout = VERIFY_TIMEOUT_SECONDS
        # The app log: audit lines for arm / match / timeout / revert
        # (surfaced in every verify payload; G12 plan item 3 "log line").
        self.app_log: list[str] = []

    def _backend_io(self, call, *args):
        """Run one backend (HID-touching) call under the controller lock."""
        with self._io_lock:
            return call(*args)

    # -- G12: app log + last-good insurance ---------------------------------

    def _log_line(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.app_log.append(f"[{stamp}] {text}")
        del self.app_log[:-VERIFY_LOG_LIMIT]

    def _factory_table(self) -> bytes:
        """Packaged factory codes (the last-good seed vector)."""
        return self.backend.default_keymap_table()

    def _record_last_good_apply(self, profile: int, layer: int, codes: bytes) -> None:
        """Persist a successfully applied matrix (G12 plan item 1).

        The applied codes ride in ``pending`` — the revertable last-good
        changes ONLY on an operator-confirmed press (item 4), so an
        unconfirmed write can never become its own revert target.
        """
        try:
            lastgood.record_apply(
                profile, layer, list(codes), self._factory_table()
            )
        except (OSError, lastgood.LastGoodError) as exc:
            self._log_line(f"last-good snapshot write failed: {exc}")

    # -- G12: verify-armed matcher + auto-revert -----------------------------

    def arm_verify(
        self, slot: object, expect: object, timeout: float | None = None
    ) -> dict[str, Any]:
        """POST /api/verify-remap: arm the matcher for a written slot.

        ``expect`` is the list of HID codes the remapped key must now emit;
        the server maps them to KeyboardEvent.code variants (explicit table,
        incl. NumLock-dependent keypad variants) and starts the timeout. On
        expiry with no matching keydown the last-good matrix is written back
        — ONLY if an operator has previously confirmed a press for this pair
        (item 4: a seed state is never auto-written; it is flagged honestly).
        """
        try:
            slot_index = int(slot)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return {
                "ok": False,
                "message": "verify-remap needs an integer 'slot'.",
                "verify": self.verify_view(),
            }
        if not 0 <= slot_index < KEYMAP_SLOTS:
            return {
                "ok": False,
                "message": f"Keymap slot must be 0..{KEYMAP_SLOTS - 1}, got {slot_index}.",
                "verify": self.verify_view(),
            }
        if not isinstance(expect, (list, tuple)) or not expect:
            return {
                "ok": False,
                "message": "verify-remap needs a non-empty 'expect' list of HID codes.",
                "verify": self.verify_view(),
            }
        codes: list[int] = []
        for item in expect:
            try:
                code = int(item)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "message": "verify-remap 'expect' entries must be integers.",
                    "verify": self.verify_view(),
                }
            if not 0 <= code <= 0xFF:
                return {
                    "ok": False,
                    "message": "verify-remap 'expect' entries must be 0x00-0xFF.",
                    "verify": self.verify_view(),
                }
            codes.append(code)
        profile = int(getattr(self.backend, "profile", 1))
        layer = int(getattr(self.backend, "layer", 1))
        try:
            lastgood.ensure_seed(profile, layer, self._factory_table())
        except (OSError, lastgood.LastGoodError) as exc:
            return {
                "ok": False,
                "message": f"verify-remap cannot seed last-good: {exc}",
                "verify": self.verify_view(),
            }
        seconds = self._verify_timeout if timeout is None else float(timeout)
        armed = _VerifyArmed(
            slot_index,
            tuple(codes),
            frozenset(v for code in codes for v in key_event_variants(code)),
            profile,
            layer,
            seconds,
        )
        with self._verify_lock:
            if self._verify_armed is not None and self._verify_armed.timer is not None:
                self._verify_armed.timer.cancel()
            self._verify_armed = armed
            self._verify_state = "armed"
            self._verify_banner = VERIFY_ARM_BANNER
            armed.timer = threading.Timer(seconds, self._on_verify_timeout, args=(armed,))
            armed.timer.daemon = True
            armed.timer.start()
        expect_text = " ".join(f"0x{code:02X}" for code in codes)
        self._log_line(
            f"verify-armed: slot {slot_index} expects [{expect_text}] on profile "
            f"{profile} layer {layer} ({seconds:.0f} s)"
        )
        return {
            "ok": True,
            "message": VERIFY_ARM_BANNER,
            "verify": self.verify_view(),
        }

    def record_key_event(self, code: object, key: object = None) -> dict[str, Any]:
        """POST /api/keyevent: one keydown observed while the app had focus.

        No storage beyond the armed verification pending (G12 plan item 2):
        the event is a live signal, matched against the armed expectation and
        dropped. A match cancels the auto-revert, marks the last-good verified
        and reports the verdict; everything else is a silent no-op.
        """
        if not isinstance(code, str) or not code:
            return {"ok": False, "message": "keyevent needs a non-empty string 'code'."}
        with self._verify_lock:
            armed = self._verify_armed
            if armed is None or code not in armed.variants:
                # Dropped: no arm, or a keydown that matches nothing. verify_view()
                # takes the same non-reentrant lock, so it runs after release.
                dropped = True
            else:
                dropped = False
                if armed.timer is not None:
                    armed.timer.cancel()
                self._verify_armed = None
                self._verify_state = "verified"
                matched_code = next(
                    (c for c in armed.expect if code in key_event_variants(c)),
                    armed.expect[0],
                )
                banner = VERIFY_MATCH_TEMPLATE.format(
                    key=key if isinstance(key, str) and key else code,
                    name=code_to_name(matched_code),
                )
                self._verify_banner = banner
                profile, layer, slot = armed.profile, armed.layer, armed.slot
        if dropped:
            return {"ok": True, "recorded": False, "verify": self.verify_view()}
        confirmed = lastgood.confirm(profile, layer)
        if confirmed is None:
            self._log_line(
                f"verify-verified: keydown {code} matched slot {slot} — last-good "
                f"NOT marked (no snapshot file)"
            )
        else:
            self._log_line(
                f"verify-verified: keydown {code} matched slot {slot} — last-good "
                f"snapshot for profile {profile} layer {layer} marked verified"
            )
        return {"ok": True, "recorded": True, "message": banner, "verify": self.verify_view()}

    def _on_verify_timeout(self, armed: _VerifyArmed) -> None:
        """60 s with no matching keydown: self-heal (G12 plan items 3 + 4)."""
        with self._verify_lock:
            if self._verify_armed is not armed:
                return  # cancelled (matched / rearmed) — stale timer
            self._verify_armed = None
        state = lastgood.load(armed.profile, armed.layer)
        if state is not None and state["verified"]:
            try:
                self._auto_revert_write(armed.profile, armed.layer, state["codes"])
            except BackendError as exc:
                self._verify_state = "revert-failed"
                self._verify_banner = VERIFY_REVERT_FAILED_TEMPLATE.format(error=exc)
                self._log_line(
                    f"verify-timeout: auto-revert FAILED for profile "
                    f"{armed.profile} layer {armed.layer}: {exc}"
                )
                return
            self._verify_state = "reverted"
            self._verify_banner = VERIFY_REVERT_BANNER
            self._log_line(
                f"verify-timeout: no input seen in {self._verify_timeout:.0f} s — "
                f"auto-reverted profile {armed.profile} layer {armed.layer} to "
                "last-good (single cmd-0x01 bind + cmd-0x03 matrix, proven path)"
            )
            return
        # Item 4: NO auto-write on an unverified state. The seed is only the
        # unchanged factory table — safe in principle, unconfirmed in fact:
        # the banner says so honestly, the keyboard stays untouched.
        banner = (
            VERIFY_SEED_NO_REVERT_BANNER
            if state is not None and state["seed"]
            else VERIFY_NO_REVERT_BANNER
        )
        self._verify_state = "no-input-no-revert"
        self._verify_banner = banner
        self._log_line(
            f"verify-timeout: no input seen and no operator-verified last-good "
            f"for profile {armed.profile} layer {armed.layer} — NOT auto-reverted "
            + ("(seed = unchanged factory table)" if state is not None and state["seed"] else "")
        )

    def _auto_revert_write(self, profile: int, layer: int, codes: list[int]) -> None:
        """Write the last-good matrix back — the proven recovery path: one
        cmd-0x01 bind + one cmd-0x03 full-matrix write (G11 recovery recipe,
        remap/KEYMAP_MAP.md EMPIRICAL-FIRMWARE note). No per-key writes, no
        retry storms: exactly one exchange per command."""

        def _revert() -> None:
            try:
                self.backend.change_profile(profile, layer)  # cmd 0x01 bind
            except BackendError as exc:
                # The WB blank mirror can never verify a bind (G8); the bind is
                # best-effort — the healing write below must not die with it.
                self._log_line(
                    f"verify-revert: profile {profile} layer {layer} bind "
                    f"unverified ({exc}) — continuing with the matrix write"
                )
            # G15: the same types overlay the apply path writes — the
            # last-good vector is a GUI-applied codes array, and restoring it
            # with the vendor 0xFF defaults would re-kill every assigned
            # 0xFF-type cell (the exact caps cell of the incident).
            codes_bytes = bytes(codes)
            default_types = self._default_types()
            effective_types = (
                build_effective_types(codes_bytes, default_types)
                if default_types is not None
                else None
            )
            self.backend.set_keymap_codes(  # cmd 0x03
                profile, layer, codes_bytes, effective_types
            )

        self._backend_io(_revert)

    def _bind_first_matrix_write(
        self, label: str, profile: int, layer: int, write
    ) -> str:
        """G16: bind the target profile/layer (cmd 0x01) IMMEDIATELY BEFORE
        one matrix write (cmd 0x03) — mirrors the proven recovery recipe
        (``_auto_revert_write`` / the G11 recovery, remap/KEYMAP_MAP.md
        EMPIRICAL-FIRMWARE note: cmd 0x01 (p,l) then cmd 0x03, both times
        the board physically answered).

        The GUI's profile/layer is a software counter (the wireless board's
        GET mirror is blank — no readback), so FN+CTRL+↑, a re-pair or a
        sleep drift the real active profile away from it, and a matrix write
        aimed at a stale pair is indistinguishable from NO write — the
        operator-confirmed 13:07 NO CHANGE. Binding first makes every GUI
        write land on exactly the profile the banner names.

        The bind is safe/reversible: it only selects the write target and
        visibly switches the board's active profile, which the operator can
        do with FN+CTRL+↑ anyway. A blank mirror can never confirm a bind
        (G8), so a failed bind verification is logged and the healing write
        proceeds: one bind + exactly ONE cmd 0x03, no blind retries.
        """
        self._log_line(
            f"{label}: bind ({profile},{layer}) -> matrix {KEYMAP_SLOTS} slots"
        )
        try:
            self.backend.change_profile(profile, layer)  # cmd 0x01
        except BackendError as exc:
            self._log_line(
                f"{label}: profile {profile} layer {layer} bind unverified "
                f"({exc}) — continuing with the matrix write"
            )
        return write()  # exactly one cmd 0x03 KeyMatrix

    def verify_view(self) -> dict[str, Any]:
        """Snapshot of the verify-armed state for the UI (and tests)."""
        with self._verify_lock:
            armed = self._verify_armed
            return {
                "armed": armed is not None,
                "state": self._verify_state,
                "banner": self._verify_banner if self._verify_state else None,
                "slot": armed.slot if armed is not None else None,
                "expect": list(armed.expect) if armed is not None else [],
                "timeout_seconds": self._verify_timeout,
                "log": list(self.app_log[-20:]),
            }

    def get_state(self) -> dict[str, Any]:
        devices = detect_gmmk_pro()
        grouped_devices = _group_devices(devices)
        if not devices:
            return {
                "ok": False,
                "message": "No GMMK Pro detected in /sys/class/hidraw.",
                "backend": self.backend.name,
                "devices": [],
                "state": None,
            }

        if not self.backend.is_available():
            return {
                "ok": False,
                "message": self.backend.install_hint(),
                "backend": self.backend.name,
                "devices": grouped_devices,
                "state": None,
            }

        try:
            state = self._backend_io(self.backend.read_state)
        except BackendError as exc:
            return {
                "ok": False,
                "message": str(exc),
                "backend": self.backend.name,
                "devices": grouped_devices,
                "state": None,
            }

        selected = grouped_devices[0]
        hidraws = ", ".join(selected["hidraws"])
        return {
            "ok": True,
            "message": f"Connected to {selected['name']} across {hidraws} using {self.backend.name}.",
            "backend": self.backend.name,
            "devices": grouped_devices,
            "state": {
                "brightness": state.brightness,
                "effect": state.effect,
                "speed": state.speed,
                "hue": state.hue,
                "saturation": state.saturation,
                "red": state.red,
                "green": state.green,
                "blue": state.blue,
            },
            "effects": getattr(self.backend, "EFFECT_LABELS", {}),
            "effect_color_groups": getattr(self.backend, "EFFECT_COLOR_GROUPS", {}),
            "colors": list(self.backend.COLOR_NAMES),
        }

    def _default_types(self) -> bytes | None:
        """Packaged factory types table, for UI type labels only (never
        written by the panel; writes send it via the backend unchanged)."""
        try:
            return (DATA_DIR / "default_types.bin").read_bytes()
        except OSError:
            return None

    def get_keystate(self) -> dict[str, Any]:
        """Keymap panel state for the ACTIVE profile/layer (the GET config
        mirror only exposes the active pair — remap/PROTOCOL_DECODE.md).

        Shadow handling (G6 verdict): after a factory reset the mirror codes
        table reads all-zero forever while writes physically land. That blank
        table is NOT displayed: the panel shows the packaged factory defaults
        (``default_keymap_table()``) with ``is_dirty_source`` set, and the UI
        shows KEYMAP_SHADOW_BANNER.

        G8 live-device case: on the WB board the whole GET response is blank
        (header included), so the strict mirror read raises — the panel then
        degrades the same way (factory defaults, ``source``
        "factory-defaults", ok:true, KEYMAP_BLANK_MIRROR_MESSAGE) instead of
        losing the remapping panel; non-blank failures stay ok:false.
        """
        devices = detect_gmmk_pro()
        grouped_devices = _group_devices(devices)
        if not devices:
            return {
                "ok": False,
                "message": "No GMMK Pro detected in /sys/class/hidraw.",
                "backend": self.backend.name,
                "devices": [],
                "keymap": None,
            }
        if not self.backend.is_available():
            return {
                "ok": False,
                "message": self.backend.install_hint(),
                "backend": self.backend.name,
                "devices": grouped_devices,
                "keymap": None,
            }

        profile = int(getattr(self.backend, "profile", 1))
        layer = int(getattr(self.backend, "layer", 1))
        shadow_blank_mirror = False
        try:
            entries = self._backend_io(self.backend.read_keymap, profile, layer)
            factory = self.backend.default_keymap_table()
        except BackendError as exc:
            # G8: the live WB board's GET mirror is fully blank (parse-None
            # signature from backend._read_config_mirror), so read_keymap
            # raises — the panel must DEGRADE, not error: packaged factory
            # defaults with an honest message, ok stays true. Non-blank
            # failures (device gone, permission, ...) still surface ok:false.
            if KEYMAP_BLANK_MIRROR_SIGNATURE not in str(exc):
                return {
                    "ok": False,
                    "message": str(exc),
                    "backend": self.backend.name,
                    "devices": grouped_devices,
                    "keymap": None,
                }
            shadow_blank_mirror = True
            factory = self.backend.default_keymap_table()
            entries = [
                KeymapEntry(slot=slot, scancode=byte, label=KEYMAP_SLOT_LABELS.get(slot))
                for slot, byte in enumerate(factory)
            ]

        codes = [int(entry.scancode) for entry in entries]
        is_dirty_source = shadow_blank_mirror or all(code == 0x00 for code in codes)
        if shadow_blank_mirror:
            source = "factory-defaults"
        elif is_dirty_source:
            source = "factory-default"
        else:
            source = "device"
        if is_dirty_source and not shadow_blank_mirror:
            # Blank shadow buffer: display the packaged factory defaults.
            codes = [int(byte) for byte in factory]

        types = self._default_types()
        # G17 (retracts G12 item 6): NO display overlay. The G13 cell sweep
        # put CapsLock on slot 60, where the packaged factory table already
        # carries 0x39 · normal — the factory-defaults degradation renders
        # the caps cell honestly on the raw vendor bytes; the slot-58 overlay
        # covered a claim the sweep refuted.
        # G15 item 2: the panel reports the EFFECTIVE types — the same
        # overlay apply-keymap writes. Slot 60 (code 0x39, vendor type 0x01)
        # shows type "normal", matching what the firmware honors;
        # type_name + cell_active give the UI the label and the "cell active"
        # boolean directly.
        display_codes = list(codes)
        display_types = (
            build_effective_types(bytes(display_codes), types)
            if types is not None
            else None
        )
        slots = []
        for entry in entries:
            code = display_codes[entry.slot]
            type_byte = (
                display_types[entry.slot]
                if display_types is not None and entry.slot < len(display_types)
                else None
            )
            type_label = KEYMAP_TYPE_LABELS.get(type_byte, "reserved")
            slots.append(
                {
                    "slot": entry.slot,
                    "code": code,
                    "code_name": code_to_name(code),
                    "code_type": type_byte,
                    "code_type_label": type_label,
                    # G15 item 2: effective-type surface for the UI.
                    "type_name": type_label,
                    "cell_active": type_byte is not None and type_byte != 0xFF,
                    "physical": KEYMAP_SLOT_LABELS.get(entry.slot),
                    "is_unassigned": code == KEYMAP_UNASSIGNED,
                    "factory_code": int(factory[entry.slot]),
                    # G9 FIX A + G10: False for the 13 matrix positions that
                    # carry no keycap (see NON_KEYCAP_SLOTS, incl. the
                    # head-of-block spares 55-59). The UI renders those as
                    # invisible spacers — a "#NNN" label never appears.
                    "is_keycap": entry.slot not in NON_KEYCAP_SLOTS,
                }
            )

        can_switch = [
            {
                "profile": candidate_profile,
                "layer": candidate_layer,
                "active": (candidate_profile, candidate_layer) == (profile, layer),
                "hint": (
                    ""
                    if (candidate_profile, candidate_layer) == (profile, layer)
                    else KEYMAP_SWITCH_HINT
                ),
            }
            for candidate_profile in KEYMAP_PROFILES
            for candidate_layer in KEYMAP_LAYERS
        ]

        message = f"Keymap mirror read for profile {profile} layer {layer}."
        if shadow_blank_mirror:
            message = KEYMAP_BLANK_MIRROR_MESSAGE
        elif is_dirty_source:
            message = (
                f"{KEYMAP_SHADOW_BANNER} — writes land on the device, the mirror "
                "cannot confirm them; physical key test is the only confirmation."
            )
        return {
            "ok": True,
            "message": message,
            "backend": self.backend.name,
            "devices": grouped_devices,
            # G12: verify-armed state (banner + verdict + recent log lines)
            # rides on every keystate fetch so the drawer banner refreshes.
            "verify": self.verify_view(),
            "keymap": {
                "profile": profile,
                "layer": layer,
                "source": source,
                "is_dirty_source": is_dirty_source,
                "slots": slots,
                "can_switch": can_switch,
                "active_profile": profile,
                "active_layer": layer,
            },
            # Remap-target catalog for the UI search dropdown (group + type
            # marker + G9 FIX C bounded search aliases, keycodes.CODE_ALIASES).
            "codes": [
                {
                    "code": code,
                    "name": code_to_name(code),
                    "group": code_group(code),
                    "target_type": target_type(code),
                    "aliases": list(code_aliases(code)),
                }
                for code in all_assignable_codes()
            ],
        }

    def matrix_self_test(self) -> dict[str, Any]:
        """G10 FIX 5: read-only matrix scan (opt-in ONLY — button click /
        CLI flag; the GUI never fires it on its own).

        Reports which GUI-labeled keys are alive per the map by auditing the
        config mirror against the slot table (keymap-slots.js /
        remap/KEYMAP_MAP.md). It performs a config-mirror GET — the same
        traffic as opening the keymap panel — and calls NO writer: apply /
        reset / switch-profile are never reached. On the G8 blank-mirror
        signature it degrades to auditing the packaged factory defaults,
        honestly labelled.
        """
        detected = detect_gmmk_pro()
        self.backend = self.backend or create_backend()
        if hasattr(self.backend, "open") and detected:
            self.backend.open(detected[0])
        try:
            if not self.backend.is_available():
                return {
                    "ok": False,
                    "message": "Matrix self-test needs the keyboard: "
                    + self.backend.install_hint(),
                    "self_test": None,
                }
            profile = int(getattr(self.backend, "profile", 1))
            layer = int(getattr(self.backend, "layer", 1))
            notes: list[str] = []
            try:
                entries = self._backend_io(self.backend.read_keymap, profile, layer)
                factory = self.backend.default_keymap_table()
                codes = [int(entry.scancode) for entry in entries]
                source = f"device mirror (profile {profile} layer {layer})"
                if all(code == 0x00 for code in codes):
                    # G6/G8 shadow buffer: all-zero mirror is not a keymap.
                    codes = [int(byte) for byte in factory]
                    source = "packaged factory defaults"
                    notes.append(
                        "device mirror is all-zero (shadow buffer) — auditing the "
                        "packaged factory defaults instead"
                    )
            except BackendError as exc:
                if KEYMAP_BLANK_MIRROR_SIGNATURE not in str(exc):
                    return {"ok": False, "message": str(exc), "self_test": None}
                factory = self.backend.default_keymap_table()
                codes = [int(byte) for byte in factory]
                source = "packaged factory defaults"
                notes.append(KEYMAP_BLANK_MIRROR_MESSAGE)
            report = build_matrix_self_test_report(codes, factory, source, notes)
            return {
                "ok": True,
                "message": "Matrix self-test complete — read-only scan, no writes sent.",
                "self_test": report,
            }
        finally:
            if hasattr(self.backend, "close"):
                self.backend.close()

    def run_action(self, action: str, value: Any = None) -> dict[str, Any]:
        bind_write_banner: str | None = None
        try:
            if action == "refresh":
                return self.get_state()
            if action == MATRIX_SELF_TEST_ACTION:
                # G10: opt-in read-only scan; never fired on page load.
                return self.matrix_self_test()
            if action == "set_brightness":
                message = self._backend_io(self.backend.set_brightness, int(value), True)
            elif action == "set_effect":
                message = self._backend_io(self.backend.set_effect, int(value), True)
            elif action == "set_speed":
                message = self._backend_io(self.backend.set_speed, int(value), True)
            elif action == "set_hue":
                message = self._backend_io(self.backend.set_hue, int(value), True)
            elif action == "set_saturation":
                message = self._backend_io(self.backend.set_saturation, int(value), True)
            elif action == "set_color":
                message = self._backend_io(self.backend.set_color_name, str(value), True)
            elif action == "set_custom_color":
                if not isinstance(value, dict):
                    return {"ok": False, "message": "Custom color payload must be an object."}
                message = self._backend_io(
                    self.backend.set_rgb_color,
                    int(value.get("red", 0)),
                    int(value.get("green", 0)),
                    int(value.get("blue", 0)),
                    True,
                    str(value.get("group")) if value.get("group") is not None else None,
                )
            elif action == "save":
                message = self._backend_io(self.backend.save)
            elif action == "device_indication":
                message = self._backend_io(self.backend.device_indication)
            elif action == "apply-keymap":
                raw = value.get("codes") if isinstance(value, dict) else None
                if (
                    not isinstance(raw, list)
                    or len(raw) != KEYMAP_SLOTS
                    or not all(
                        isinstance(code, int)
                        and not isinstance(code, bool)
                        and 0 <= code <= 0xFF
                        for code in raw
                    )
                ):
                    return {
                        "ok": False,
                        "message": f"Keymap payload must carry {KEYMAP_SLOTS} integer codes (0-255).",
                    }
                profile = int(getattr(self.backend, "profile", 1))
                layer = int(getattr(self.backend, "layer", 1))
                raw_bytes = bytes(raw)
                # G15: the GUI payload carries CODES only; pairing them with
                # the packaged vendor types verbatim leaves every assigned
                # 0xFF-type slot INACTIVE — the firmware honors the type byte
                # as cell activation. Send the effective types overlay
                # explicitly.
                default_types = self._default_types()
                effective_types = (
                    build_effective_types(raw_bytes, default_types)
                    if default_types is not None
                    else None
                )
                # G16: bind (cmd 0x01) immediately before the matrix write
                # (cmd 0x03) — a profile-drifted GUI counter otherwise sends
                # the matrix to a profile the operator is not typing on (the
                # 13:07 NO CHANGE). One lock: bind+write are atomic.
                message = self._backend_io(
                    self._bind_first_matrix_write,
                    "apply-keymap",
                    profile,
                    layer,
                    lambda: self.backend.set_keymap_codes(
                        profile, layer, raw_bytes, effective_types
                    ),
                )
                # G12 item 1: persist every applied matrix as last-good
                # insurance. Runs OUTSIDE the io lock's backend call; the
                # applied codes ride in `pending` and only become the
                # revertable state on an operator-confirmed press (item 4).
                self._record_last_good_apply(profile, layer, bytes(raw))
                bind_write_banner = KEYMAP_BIND_WRITE_BANNER.format(
                    profile=profile, layer=layer
                )
            elif action == "reset-keymap":
                profile = int(getattr(self.backend, "profile", 1))
                layer = int(getattr(self.backend, "layer", 1))
                # G16: same bind-first rule for the factory-reset write.
                message = self._backend_io(
                    self._bind_first_matrix_write,
                    "reset-keymap",
                    profile,
                    layer,
                    lambda: self.backend.reset_keymap(profile, layer),
                )
                bind_write_banner = KEYMAP_BIND_WRITE_BANNER.format(
                    profile=profile, layer=layer
                )
            elif action == "switch-profile":
                if not isinstance(value, dict):
                    return {"ok": False, "message": "Profile payload must be an object with profile and layer."}
                try:
                    profile = int(value.get("profile", 0))
                    layer = int(value.get("layer", 0))
                except (TypeError, ValueError):
                    return {"ok": False, "message": "profile and layer must be integers."}
                if profile not in KEYMAP_PROFILES or layer not in KEYMAP_LAYERS:
                    return {"ok": False, "message": "profile and layer must be between 1 and 3."}
                message = self._backend_io(self.backend.change_profile, profile, layer)
            else:
                return {"ok": False, "message": f"Unknown action: {action}"}
        except BackendError as exc:
            return {"ok": False, "message": str(exc)}

        state = self.get_state()
        state["message"] = message
        if bind_write_banner is not None:
            # G16 item 3: the drawer banner names the exact profile/layer the
            # bind-first write targeted (the UI only paints this sentence).
            state["keymap_write_banner"] = bind_write_banner
        return state


class AppRequestHandler(SimpleHTTPRequestHandler):
    controller: OpenGloriousCoreWebController

    def __init__(self, *args, directory: str | None = None, controller: OpenGloriousCoreWebController | None = None, **kwargs) -> None:
        self.controller = controller or OpenGloriousCoreWebController()
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._send_json(self.controller.get_state())
            return
        if self.path == "/api/keystate":
            self._send_json(self.controller.get_keystate())
            return
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        if self.path not in ("/api/action", "/api/verify-remap", "/api/keyevent"):
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json({"ok": False, "message": "Invalid JSON payload."}, status=HTTPStatus.BAD_REQUEST)
            return
        if not isinstance(payload, dict):
            payload = {}

        if self.path == "/api/verify-remap":
            # G12: arm the matcher for a just-written slot (60 s auto-revert).
            result = self.controller.arm_verify(payload.get("slot"), payload.get("expect"))
            self._send_json(result, status=HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST)
            return
        if self.path == "/api/keyevent":
            # G12: one keydown observed by the page logger. Matched against
            # the armed expectation only — no storage beyond that.
            result = self.controller.record_key_event(payload.get("code"), payload.get("key"))
            self._send_json(result, status=HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST)
            return

        result = self.controller.run_action(payload.get("action", ""), payload.get("value"))
        self._send_json(result, status=HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_matrix_self_test_cli() -> int:
    """G10 FIX 5 CLI form: print the read-only matrix audit and exit — no
    server, no Qt window. Opt-in only (``--self-test-matrix``); performs no
    writes (same read-only path as the GUI button)."""
    controller = OpenGloriousCoreWebController()
    result = controller.matrix_self_test()
    report = result.get("self_test")
    if report:
        for line in report:
            print(line)
        print(result.get("message", ""))
        return 0
    print(f"Matrix self-test unavailable: {result.get('message', 'unknown error')}")
    return 1


def main() -> None:
    if MATRIX_SELF_TEST_FLAG in sys.argv[1:]:
        sys.exit(run_matrix_self_test_cli())

    controller = OpenGloriousCoreWebController()

    def handler(*args, **kwargs):
        return AppRequestHandler(*args, directory=str(STATIC_DIR), controller=controller, **kwargs)

    server = ThreadingHTTPServer((HOST, 0), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    server_thread = threading.Thread(target=server.serve_forever, name="open-glorious-core-ui-server", daemon=True)

    print(f"Open Glorious Core desktop app serving UI at {url}")
    print(f"Edit UI files in: {STATIC_DIR}")
    server_thread.start()

    qt_app = QApplication.instance() or QApplication(sys.argv)
    if ICON_PATH.exists():
        qt_app.setWindowIcon(QIcon(str(ICON_PATH)))

    view = QWebEngineView()
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    if ICON_PATH.exists():
        view.setWindowIcon(QIcon(str(ICON_PATH)))
    view.setWindowTitle("Open Glorious Core")
    view.resize(1240, 900)
    view.setMinimumSize(960, 720)
    view.load(QUrl(url))
    view.show()

    try:
        qt_app.exec()
    except KeyboardInterrupt:
        print()
        print("Shutting down Open Glorious Core desktop UI...")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
