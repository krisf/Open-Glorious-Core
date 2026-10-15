from __future__ import annotations

import json
import sys
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

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
ICON_PATH = STATIC_DIR / "assets" / "glorious-core.ico"
DATA_DIR = Path(__file__).with_name("data")
HOST = "127.0.0.1"

# Keymap panel constants. The types table ships as the packaged factory
# default and is used for UI type LABELS only (never written unless the types
# file says so — writes send data/default_types.bin).
KEYMAP_TYPE_LABELS = {0x01: "normal", 0x02: "modifier", 0x03: "consumer", 0xFF: "unused"}
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
#   * 55, 56, 57, 59 — the head of the row-4 USB block, BEFORE Caps Lock:
#     factory 0xFF cells with no empirical identity (KEYMAP_MAP.md lines
#     115-119; data/default_keymap.bin[55..57,59] are 0xFF).
#   * 60 — the vendor Buttoninfo_Default's CapsLock cell (factory 0x39). G11
#     operator proof on this WB5092 firmware (320F:5092, 2026-10-15): the
#     physical CapsLock cell is slot 58 — pushing the factory vector (0xFF at
#     58) killed CapsLock and a recovery write of 0x39 to 58 restored it,
#     while every write aimed at 60 did nothing. Slot 60 therefore has no
#     empirical identity: it carries a harmless duplicate 0x39, is hidden from
#     the grid like the 55/56/57/59 cells, and must NOT be touched without an
#     operator-confirmed plan.
NON_KEYCAP_SLOTS = frozenset({14, 55, 56, 57, 59, 60, 72, 92, 103, 104, 106, 107, 111})
KEYMAP_BLANK_MIRROR_MESSAGE = (
    "Config mirror is blank (shadow buffer never populates on this wireless "
    "board post-factory-reset); showing packaged factory defaults. Writes "
    "still land and physical key test is the only verification."
)

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
        "G11 empirical firmware truth (WB5092, operator-verified): CapsLock = "
        "slot 58; the vendor table (58=0xFF, 60=0x39) is wrong — a live 0x39 "
        "at 58 is the working caps cell, NOT a deviation.",
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
    lines.append("  55, 56, 57, 59             head of row-4 block, factory 0xFF, "
                 "no empirical identity — dead-write cells (G10)")
    lines.append("  60                         vendor-table caps cell (factory 0x39): "
                 "duplicate caps, no empirical identity — hidden, G11; do not touch "
                 "without an operator-confirmed plan")
    lines.append("  103, 104, 106, 107, 111   row-6 spacers, factory 0xFF")
    hidden_no_cap = (55, 56, 57, 59, 60)
    bound_hidden = any(
        slot in hidden_no_cap
        for row in SLOT_TABLE["rows"]
        for slot, key in zip(row["slots"], row["keys"])
        if key is not None
    )
    lines.append(
        f"Caps bound: {SELF_TEST_CAPS_BOUND}; slots 55-57, 59, 60 bound to a "
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


class OpenGloriousCoreWebController:
    def __init__(self) -> None:
        self.backend = create_backend()
        # One HID conversation at a time: ThreadingHTTPServer hands every
        # request its own thread, and the feature-report interface is a
        # single shared device — never interleave report exchanges.
        self._io_lock = threading.Lock()

    def _backend_io(self, call, *args):
        """Run one backend (HID-touching) call under the controller lock."""
        with self._io_lock:
            return call(*args)

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
        slots = []
        for entry in entries:
            code = codes[entry.slot]
            type_byte = types[entry.slot] if types is not None and entry.slot < len(types) else None
            slots.append(
                {
                    "slot": entry.slot,
                    "code": code,
                    "code_name": code_to_name(code),
                    "code_type_label": KEYMAP_TYPE_LABELS.get(type_byte, "reserved"),
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
                message = self._backend_io(
                    self.backend.set_keymap_codes, profile, layer, bytes(raw)
                )
            elif action == "reset-keymap":
                profile = int(getattr(self.backend, "profile", 1))
                layer = int(getattr(self.backend, "layer", 1))
                message = self._backend_io(self.backend.reset_keymap, profile, layer)
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
        if self.path != "/api/action":
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json({"ok": False, "message": "Invalid JSON payload."}, status=HTTPStatus.BAD_REQUEST)
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
