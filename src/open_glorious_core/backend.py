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
CMD_KEY_MATRIX = 0x03  # the keymap WRITE (SendButtonMatrix2Device)
CMD_GET_CONFIG = 0x81  # 129: config-mirror read (our proven read path)

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
# remap/KEYMAP_MAP.md). Slots 14 / 72 / 92 carry non-FF raw bytes in the factory
# dump but have no confirmed keycap on this unit and are deliberately unlabeled.
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
        The config mirror is read first — the mirror only exposes
        the ACTIVE profile, so writing a non-active slot is refused instead of
        silently hitting the wrong slot — then ONE report is sent (never
        retried) and the mirror re-read to byte-compare the codes table.

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
        self._read_config_mirror(profile, layer)  # active-slot guard + device presence
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

        Reads the mirror, patches the slots, then sends the whole patched
        matrix through cmd 0x03 (the vendor protocol has no per-key write).
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
        mirror = self._read_config_mirror(profile, layer)
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
        """Restore factory defaults: packaged default codes + default types.

        There is no dedicated reset command: "reset" means writing the captured
        factory table back with the same cmd-0x03 matrix write, using the
        vendor Buttoninfo_Default types array. The table written comes from the
        shipped dumps, never from what the device mirrors; the mirror is still
        read first so a write to a NON-active profile/layer is refused.
        """
        self._read_config_mirror(profile, layer)  # active-slot guard + device presence
        shadow = self._write_keymap_matrix(
            self._default_keymap_table(), self._default_types(), profile, layer
        )
        if shadow:
            return self._shadow_write_message("the factory default keymap", profile, layer)
        return (
            f"Restored the factory default keymap on profile {profile} layer {layer}; "
            "read-back verified against the full codes table."
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

    def _read_config_mirror(self, profile: int, layer: int) -> bytearray:
        """GET report 7 (via the proven cmd-129 exchange) -> config mirror."""
        if not self.hidraw:
            raise BackendError(self.install_hint())
        for name, value in (("profile", profile), ("layer", layer)):
            if not 1 <= int(value) <= 3:
                raise BackendError(f"{name.capitalize()} must be 1-3, got {value!r}.")
        request = self._make_request(CMD_GET_CONFIG)
        request[2] = profile
        request[3] = layer
        response = self._exchange_feature_report(request)
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
        """Send the matrix once, then byte-verify the mirror's codes table.

        Returns True for the factory-reset SHADOW case (remap/PROTOCOL_DECODE.md
        G6 verdict): the read-back codes table is the all-zero blank shadow
        (KEYMAP_BLANK_TABLE). The mirror is push-only and stays blank forever
        after a factory reset even though cmd 0x03 writes physically land —
        an empty table is "read-back cannot verify", NOT a mismatch, so it
        must not raise; callers return the shadow-caveat success message
        instead. Returns False when the read-back byte-matches the payload.
        Any other difference is a true mismatch and raises.
        """
        payload = self._build_key_matrix_report(profile, layer, codes, types)
        self._send_feature_report(payload)
        verified = self._read_config_mirror(profile, layer)
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
        self._send_feature_report(profile_update)

    def _prepare_properties_buffer(self, report: bytearray) -> None:
        report[0] = self.REPORT_ID
        report[1] = 7
        report[2] = self.profile
        report[3] = self.layer
        report[4] = self.state.effect
        report[5] = 0
        report[6] = 1
        report[7] = 0
        report[8] = round(self.state.brightness * 20 / 100)
        report[10] = 16

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
