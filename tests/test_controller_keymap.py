"""G7 Commit 3 — keymap controller API tests (GET /api/keystate + POST actions).

No Qt, no HTTP server, no device: the controller is exercised directly with a
stub backend swapped in via ``app.create_backend`` and ``detect_gmmk_pro``
monkeypatched (conftest keeps real HID IO hard-blocked).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_glorious_core import app as app_module
from open_glorious_core.backend import (
    KEYMAP_SLOTS,
    BackendError,
    KeymapEntry,
)
from open_glorious_core.device import HidDevice

ROOT = Path(__file__).resolve().parents[1]
FACTORY = (ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin").read_bytes()
# G12 item 6: the keystate factory-defaults degradation displays slot 58 with
# the EMPIRICAL overlay (Caps Lock 0x39 · normal), not the vendor "Unassigned
# (0xFF)" — the misreading that hid the G11 kill. Display + write overlay
# only; FACTORY itself stays the raw vendor bytes.
FACTORY_DISPLAY = bytes(bytearray(FACTORY)[:58] + b"\x39" + bytearray(FACTORY)[59:])
TYPES = (ROOT / "src" / "open_glorious_core" / "data" / "default_types.bin").read_bytes()

DEVICE = HidDevice(
    hidraw="/dev/hidraw-test",
    name="GMMK Pro",
    vendor_id=0x218B,
    product_id=0x0A2B,
    physical_id="test-1",
    interface_id=2,
)

SHADOW_TAIL = (
    "device mirror is blank so read-back could not verify — physical key "
    "test is the only confirmation."
)
BANNER = "Device buffer not initialized — showing factory default"
# Exact G8 degradation message (plan-pinned, byte-for-byte).
BLANK_MIRROR_MESSAGE = (
    "Config mirror is blank (shadow buffer never populates on this wireless "
    "board post-factory-reset); showing packaged factory defaults. Writes "
    "still land and physical key test is the only verification."
)
# Exact backend text for the fully blank (parse-None) live-device mirror.
BLANK_MIRROR_BACKEND_ERROR = (
    "Config mirror read failed: device response carries no parsable profile/layer."
)


class FakeBackend:
    """Records every call; asserts each runs under the controller IO lock."""

    name = "Fake GMMK Pro"
    COLOR_NAMES = ["red"]
    EFFECT_LABELS = {}
    EFFECT_COLOR_GROUPS = {}

    def __init__(self, codes=None, available: bool = True) -> None:
        self.profile = 1
        self.layer = 1
        self.codes = list(FACTORY) if codes is None else list(codes)
        self.available = available
        self.calls: list[tuple] = []
        self.lock: threading.Lock | None = None  # wired to the controller lock

    # -- helpers ----------------------------------------------------------
    def _note(self, name: str, *args) -> None:
        # The lock must be held: threading.Lock is non-reentrant, so a
        # locked() check from inside a backend call proves serialization.
        assert self.lock is not None and self.lock.locked(), (
            f"{name} ran outside the controller IO lock"
        )
        self.calls.append((name, *args))

    def calls_named(self, name: str) -> list[tuple]:
        return [call for call in self.calls if call[0] == name]

    # -- availability surface ---------------------------------------------
    def is_available(self) -> bool:
        return self.available

    def install_hint(self) -> str:
        return "Fake install hint."

    def read_state(self):
        self._note("read_state")
        time.sleep(0.001)
        return SimpleNamespace(
            brightness=100, effect=0, speed=50, hue=0, saturation=0,
            red=255, green=255, blue=255,
        )

    # -- keymap surface -----------------------------------------------------
    def read_keymap(self, profile: int = 1, layer: int = 1):
        self._note("read_keymap", profile, layer)
        time.sleep(0.001)
        return [KeymapEntry(slot=slot, scancode=self.codes[slot]) for slot in range(KEYMAP_SLOTS)]

    def default_keymap_table(self) -> bytes:
        return FACTORY

    def set_keymap_codes(
        self, profile: int, layer: int, codes: bytes, types: bytes | None = None
    ) -> str:
        # G15: the controller now pairs every write with an EFFECTIVE types
        # overlay — record it so the pins can assert the exact bytes.
        self._note(
            "set_keymap_codes",
            profile,
            layer,
            bytes(codes),
            None if types is None else bytes(types),
        )
        time.sleep(0.001)
        self.codes = list(codes)
        return (
            f"Wrote the full {KEYMAP_SLOTS}-slot key matrix to profile {profile} "
            f"layer {layer} (cmd 3 KeyMatrix); " + SHADOW_TAIL
        )

    def reset_keymap(self, profile: int, layer: int) -> str:
        self._note("reset_keymap", profile, layer)
        return (
            f"Wrote the factory default keymap to profile {profile} layer {layer} "
            f"(cmd 3 KeyMatrix); " + SHADOW_TAIL
        )

    def change_profile(self, profile: int, layer: int) -> str:
        self._note("change_profile", profile, layer)
        self.profile, self.layer = profile, layer
        return f"Switched to profile {profile} layer {layer}."

    # -- RGB surface (regression: lock wraps existing actions too) ----------
    def set_brightness(self, value: int, sync: bool) -> str:
        self._note("set_brightness", value, sync)
        return f"Brightness set to {value}."


def make_controller(monkeypatch: pytest.MonkeyPatch, backend: FakeBackend):
    monkeypatch.setattr(app_module, "create_backend", lambda: backend)
    controller = app_module.OpenGloriousCoreWebController()
    backend.lock = controller._io_lock
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [DEVICE])
    return controller, backend


@pytest.fixture()
def controller(monkeypatch: pytest.MonkeyPatch):
    return make_controller(monkeypatch, FakeBackend())


# ---------------------------------------------------------------------------
# GET /api/keystate
# ---------------------------------------------------------------------------


def test_keystate_normal_device_shape(controller) -> None:
    ctrl, _ = controller
    payload = ctrl.get_keystate()
    assert payload["ok"] is True
    assert payload["backend"] == "Fake GMMK Pro"
    assert payload["devices"][0]["hidraws"] == ["/dev/hidraw-test"]
    data = payload["keymap"]
    assert data["profile"] == 1 and data["layer"] == 1
    assert data["source"] == "device"
    assert data["is_dirty_source"] is False
    assert data["active_profile"] == 1 and data["active_layer"] == 1
    assert "profile 1 layer 1" in payload["message"]

    slots = data["slots"]
    assert len(slots) == KEYMAP_SLOTS
    esc = slots[0]
    assert esc["slot"] == 0
    assert esc["code"] == 0x29
    assert esc["code_name"] == "Escape (0x29)"
    assert esc["physical"] == "Esc"
    assert esc["code_type_label"] == "normal"
    assert esc["is_unassigned"] is False
    assert esc["factory_code"] == 0x29
    # Packaged types table drives the labels (slot 14 consumer, slot 80
    # modifier, slot 15 spare unused).
    assert slots[14]["code_type_label"] == "consumer"
    assert slots[80]["code_type_label"] == "modifier"
    assert slots[15]["code_type_label"] == "unused"
    assert TYPES[80] == 0x02  # ground truth for the assertion above
    # G15 item 2: the payload reports EFFECTIVE types (what a write will
    # send / the firmware will honor) plus the UI-facing type_name and the
    # cell_active boolean. Device codes are the factory table here: every
    # 0xFF-type slot is unassigned, so the effective table is self-consistent.
    assert slots[0]["type_name"] == "normal" and slots[0]["cell_active"] is True
    assert slots[14]["type_name"] == "consumer" and slots[14]["cell_active"] is True
    assert slots[80]["type_name"] == "modifier" and slots[80]["cell_active"] is True
    assert slots[58]["code_type_label"] == "unused"  # device byte 0xFF
    assert slots[58]["type_name"] == "unused"
    assert slots[58]["cell_active"] is False

    switch = data["can_switch"]
    assert len(switch) == 9
    active = [entry for entry in switch if entry["active"]]
    assert len(active) == 1 and (active[0]["profile"], active[0]["layer"]) == (1, 1)
    for entry in switch:
        if not entry["active"]:
            assert entry["hint"] == "switch-profile first"

    catalog = payload["codes"]
    assert catalog, "code catalog must ship for the UI dropdown"
    letter_a = next(entry for entry in catalog if entry["code"] == 0x04)
    assert letter_a == {
        "code": 0x04,
        "name": "A (0x04)",
        "group": "alpha",
        "target_type": 0x01,
        "aliases": [],
    }
    assert any(entry["code"] == 0xFF for entry in catalog)
    # G9 FIX C: the searchable catalog ships its bounded aliases per entry
    # (keypad digit 1: the num/numpad/kp/p family; nothing aliases a bare "1").
    keypad_1 = next(entry for entry in catalog if entry["code"] == 0x59)
    assert keypad_1["aliases"] == ["num1", "numpad1", "kp1", "p1"]
    assert all("1" not in entry["aliases"] for entry in catalog)


def test_keystate_slots_carry_is_keycap_flag(controller) -> None:
    """G9 FIX A + G10/G11: every slot object carries the per-slot is_keycap
    boolean; all 13 keycap-less matrix positions (G9's 8, the head cells
    55/56/57/59 and the hidden vendor caps duplicate 60) are False, real
    keycaps — including the empirical CapsLock at 58 — are True."""
    ctrl, _ = controller
    slots = ctrl.get_keystate()["keymap"]["slots"]
    assert app_module.NON_KEYCAP_SLOTS == {
        14, 55, 56, 57, 59, 60, 72, 92, 103, 104, 106, 107, 111
    }
    assert all("is_keycap" in slot for slot in slots)
    for slot in app_module.NON_KEYCAP_SLOTS:
        assert slots[slot]["is_keycap"] is False, slot
    # Head cells 55/56/57/59: factory 0xFF, no empirical identity, never caps.
    for slot in (55, 56, 57, 59):
        assert slots[slot]["is_keycap"] is False, slot
        assert FACTORY[slot] == 0xFF, slot
    # G11 operator-confirmed: the CapsLock cell on this WB5092 firmware is
    # slot 58 (keycap), even though the VENDOR dump puts 0xFF there.
    assert slots[58]["is_keycap"] is True
    assert FACTORY[58] == 0xFF, "vendor table lies at 58 (G11)"
    # Slot 60 is the vendor table's Caps Lock cell (0x39): hidden, no
    # empirical identity, bound to NO cap.
    assert slots[60]["is_keycap"] is False
    assert FACTORY[60] == 0x39
    # Real keycaps around the row-6 spacers: LCtrl / Space / Fn / Left.
    for slot in (100, 105, 109, 112):
        assert slots[slot]["is_keycap"] is True, slot
    # Fixture evidence for the five row-6 `··` spacers (KEYMAP_MAP.md 175-187):
    # factory code 0xFF, no keycap.
    for slot in (103, 104, 106, 107, 111):
        assert FACTORY[slot] == 0xFF, slot
    # Real caps are never 0xFF-flagged as spares: Space keeps its keycap.
    assert FACTORY[105] == 0x2C


def test_keystate_shadow_mirror_shows_factory_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _ = make_controller(monkeypatch, FakeBackend(codes=[0x00] * KEYMAP_SLOTS))
    payload = ctrl.get_keystate()
    assert payload["ok"] is True
    data = payload["keymap"]
    assert data["source"] == "factory-default"
    assert data["is_dirty_source"] is True
    # The blank shadow is NEVER displayed: packaged factory bytes instead,
    # with the G12 empirical overlay at slot 58 (Caps Lock 0x39, not 0xFF).
    assert [slot["code"] for slot in data["slots"]] == list(FACTORY_DISPLAY)
    assert data["slots"][58]["code_name"] == "Caps Lock (0x39)"
    assert data["slots"][58]["code_type_label"] == "normal"
    # G15 item 2: effective types surface. The factory table is self-
    # consistent on every vendor byte; slot 58 is the one exception — the
    # empirical overlay gives it code 0x39, whose effective type is 0x01.
    assert [slot["code_type"] for slot in data["slots"]] == [
        0x01 if slot["slot"] == 58 else TYPES[slot["slot"]] for slot in data["slots"]
    ]
    assert data["slots"][58]["type_name"] == "normal"
    assert data["slots"][58]["cell_active"] is True
    assert data["slots"][15]["type_name"] == "unused"
    assert data["slots"][15]["cell_active"] is False
    assert data["slots"][0]["code_name"] == "Escape (0x29)"
    assert payload["message"].startswith(BANNER)
    assert "physical key test" in payload["message"]


def test_keystate_blank_mirror_degrades_to_factory_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """G8: fully blank live-device mirror (parse-None BackendError) must NOT
    lose the panel — serve packaged factory defaults, ok:true, honest message."""
    backend = FakeBackend()

    def blank_read_keymap(profile=1, layer=1):
        backend._note("read_keymap", profile, layer)
        raise BackendError(BLANK_MIRROR_BACKEND_ERROR)

    ctrl, backend = make_controller(monkeypatch, backend)
    monkeypatch.setattr(backend, "read_keymap", blank_read_keymap)
    payload = ctrl.get_keystate()
    assert payload["ok"] is True
    data = payload["keymap"]
    assert data["source"] == "factory-defaults"
    assert data["is_dirty_source"] is True
    slots = data["slots"]
    assert len(slots) == KEYMAP_SLOTS == 120
    assert slots[0]["slot"] == 0
    assert slots[0]["code"] == 0x29
    # Slots are the packaged factory table (G12 empirical overlay at 58:
    # Caps Lock 0x39 · normal), never the blank device answer.
    assert [slot["code"] for slot in slots] == list(FACTORY_DISPLAY)
    assert slots[58]["code"] == 0x39
    assert slots[58]["code_name"] == "Caps Lock (0x39)"
    assert slots[58]["code_type_label"] == "normal"
    assert slots[58]["factory_code"] == 0xFF  # raw vendor byte stays visible
    # The drawer/tooltip composition the plan pins (built in JS from these):
    assert (
        f"Slot 58 · {slots[58]['code_name']} · {slots[58]['code_type_label']}"
        == "Slot 58 · Caps Lock (0x39) · normal"
    )
    # G9 FIX A: the keycap flag stamps on the degraded path too. G11: the
    # empirical CapsLock@58 is a keycap and the hidden cells (55/56/57/59/60)
    # are not, exactly as on the live path — /api/keystate and the grid agree.
    assert slots[103]["is_keycap"] is False and slots[105]["is_keycap"] is True
    assert slots[58]["is_keycap"] is True and slots[60]["is_keycap"] is False
    assert data["profile"] == 1 and data["layer"] == 1
    assert payload["message"] == BLANK_MIRROR_MESSAGE


def test_keystate_device_gone_still_ok_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """G8 degradation is ONLY for the blank-mirror signature: a non-blank
    failure (device vanished etc.) keeps the honest ok:false panel error."""
    backend = FakeBackend()

    def gone_read_keymap(profile=1, layer=1):
        backend._note("read_keymap", profile, layer)
        raise BackendError(
            "Feature report read failed on hidraw-test: [Errno 19] No such device"
        )

    ctrl, backend = make_controller(monkeypatch, backend)
    monkeypatch.setattr(backend, "read_keymap", gone_read_keymap)
    payload = ctrl.get_keystate()
    assert payload["ok"] is False
    assert payload["keymap"] is None
    assert "No such device" in payload["message"]


def test_keystate_no_device(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()
    ctrl, _ = make_controller(monkeypatch, backend)
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [])
    payload = ctrl.get_keystate()
    assert payload["ok"] is False
    assert payload["keymap"] is None
    assert payload["devices"] == []
    assert "No GMMK Pro detected" in payload["message"]
    # No device => never touches the HID surface.
    assert backend.calls == []


def test_keystate_backend_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _ = make_controller(monkeypatch, FakeBackend(available=False))
    payload = ctrl.get_keystate()
    assert payload["ok"] is False
    assert payload["keymap"] is None
    assert payload["message"] == "Fake install hint."


def test_keystate_backend_error_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = FakeBackend()

    def boom(profile=1, layer=1):
        raise BackendError("mirror read exploded")

    ctrl, _ = make_controller(monkeypatch, backend)
    monkeypatch.setattr(backend, "read_keymap", boom)
    payload = ctrl.get_keystate()
    assert payload["ok"] is False
    assert payload["keymap"] is None
    assert "mirror read exploded" in payload["message"]


# ---------------------------------------------------------------------------
# POST actions: routing, validation, passthrough
# ---------------------------------------------------------------------------


def test_apply_keymap_valid_writes_full_table(controller) -> None:
    ctrl, backend = controller
    new_codes = list(FACTORY)
    new_codes[0] = 0x68
    result = ctrl.run_action("apply-keymap", {"codes": new_codes})
    assert result["ok"] is True
    assert result["message"].startswith("Wrote the full 120-slot key matrix to profile 1 layer 1")
    writes = backend.calls_named("set_keymap_codes")
    # G15: the write carries EXPLICIT effective types, not the implicit
    # backend default. Slot 0's default type is 0x01 (protected) and every
    # factory 0xFF-type slot is unassigned here, so this vector's effective
    # table equals the packaged table — pinning that the overlay is a no-op
    # on the self-consistent factory vector, never blind byte-drift.
    assert writes == [("set_keymap_codes", 1, 1, bytes(new_codes), TYPES)]
    # Response is a full state payload (get_state) + the backend message.
    assert result["state"]["brightness"] == 100


def test_apply_keymap_effective_types_overlay(controller) -> None:
    """G15 fix spec 1, on the real controller write path: an assigned cell
    whose vendor type is 0xFF must go out type 0x01 (the caps→NumpadDivide
    incident), a modifier assignment 0x02, and the knob's 0x03 / modifier
    0x02 typings are never touched."""
    ctrl, backend = controller
    codes = list(FACTORY)
    codes[58] = 0x54  # caps cell → NumpadDivide (the operator's write)
    codes[15] = 0xE0  # spare cell → LCtrl assignment
    codes[14] = 0x68  # knob cell: REASSIGNED, its 0x03 typing stays sacred
    assert ctrl.run_action("apply-keymap", {"codes": codes})["ok"] is True
    types = backend.calls_named("set_keymap_codes")[0][4]
    assert types[58] == 0x01, "assigned 0xFF-type cell activates as normal key"
    assert types[15] == 0x02, "modifier-code assignment matches vendor typing"
    assert types[14] == 0x03, "knob consumer typing protected (rule 3)"
    for slot in (80, 91, 100, 101, 102, 108, 110):
        assert types[slot] == 0x02, "the vendor modifier set is untouched"
    for slot in range(KEYMAP_SLOTS):
        if FACTORY[slot] == 0xFF and slot not in (15, 58):
            assert types[slot] == TYPES[slot], f"unassigned slot {slot} untouched"
    # Un-assigning a cell does not sneak a type byte in.
    codes[58] = 0xFF
    ctrl.run_action("apply-keymap", {"codes": codes})
    assert backend.calls_named("set_keymap_codes")[-1][4][58] == 0xFF


def test_apply_keymap_validation(controller) -> None:
    ctrl, backend = controller
    bad = [
        None,
        {"codes": "not a list"},
        {"codes": [0x04] * (KEYMAP_SLOTS - 1)},
        {"codes": [0x04] * (KEYMAP_SLOTS + 1)},
        {"codes": [0x100] * KEYMAP_SLOTS},
        {"codes": [-1] + [0x04] * (KEYMAP_SLOTS - 1)},
        {"codes": [True] * KEYMAP_SLOTS},
        {"codes": ["0x04"] * KEYMAP_SLOTS},
    ]
    for value in bad:
        result = ctrl.run_action("apply-keymap", value)
        assert result["ok"] is False, value
        assert "120 integer codes" in result["message"]
    assert backend.calls_named("set_keymap_codes") == []


def test_reset_keymap_targets_active_pair(controller) -> None:
    ctrl, backend = controller
    backend.profile, backend.layer = 2, 3
    result = ctrl.run_action("reset-keymap")
    assert result["ok"] is True
    assert result["message"].startswith("Wrote the factory default keymap to profile 2 layer 3")
    assert backend.calls_named("reset_keymap") == [("reset_keymap", 2, 3)]


def test_switch_profile_routing(controller) -> None:
    ctrl, backend = controller
    assert ctrl.run_action("switch-profile", "2,3")["ok"] is False
    assert ctrl.run_action("switch-profile", {"profile": 4, "layer": 1})["ok"] is False
    assert ctrl.run_action("switch-profile", {"profile": 0, "layer": 3})["ok"] is False
    assert ctrl.run_action("switch-profile", {"profile": "x", "layer": 1})["ok"] is False
    assert ctrl.run_action("switch-profile", {"profile": 2, "layer": 2})["ok"] is True
    assert backend.calls_named("change_profile") == [("change_profile", 2, 2)]
    assert (backend.profile, backend.layer) == (2, 2)


def test_unknown_action_rejected(controller) -> None:
    ctrl, backend = controller
    result = ctrl.run_action("launch-missiles")
    assert result["ok"] is False
    assert "Unknown action" in result["message"]
    assert backend.calls == []


def test_rgb_action_now_locked_and_unchanged(controller) -> None:
    ctrl, backend = controller
    result = ctrl.run_action("set_brightness", "50")
    assert result["ok"] is True
    assert result["message"] == "Brightness set to 50."
    assert backend.calls_named("set_brightness") == [("set_brightness", 50, True)]


def test_backend_error_action_becomes_ok_false(controller) -> None:
    ctrl, backend = controller
    backend.available = False
    backend.profile, backend.layer = 1, 1

    def boom(profile=1, layer=1):
        raise BackendError("device vanished mid-write")

    setattr(backend, "reset_keymap", boom)
    result = ctrl.run_action("reset-keymap")
    assert result["ok"] is False
    assert "device vanished mid-write" in result["message"]


# ---------------------------------------------------------------------------
# IO lock: serialization, no deadlock
# ---------------------------------------------------------------------------


def test_lock_serializes_concurrent_requests(controller) -> None:
    ctrl, backend = controller
    errors: list[BaseException] = []
    done = threading.Event()

    def worker(index: int) -> None:
        try:
            for _ in range(3):
                assert ctrl.get_keystate()["ok"] is True
                if index % 2:
                    assert ctrl.run_action("set_brightness", "80")["ok"] is True
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        if index == 0:
            done.set()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not any(thread.is_alive() for thread in threads), "deadlock: threads hung"
    assert not errors
    assert backend.calls  # lock never dropped: every call recorded + asserted
    assert not ctrl._io_lock.locked()  # lock fully released afterwards


def test_lock_is_nonreentrant_contract(controller) -> None:
    # The FakeBackend._note assertion above would fail loudly if the
    # controller ever nested the lock (self-deadlock on acquire); sanity-check
    # the contract directly: a plain second acquire from this thread blocks.
    ctrl, _ = controller
    assert ctrl._io_lock.acquire()
    try:
        acquired = ctrl._io_lock.acquire(blocking=False)
        assert acquired is False, "controller IO lock became reentrant"
    finally:
        ctrl._io_lock.release()


# ---------------------------------------------------------------------------
# G10 FIX 5: matrix self-test aid (opt-in, READ-ONLY)
# ---------------------------------------------------------------------------


def test_matrix_self_test_scans_mirror_with_zero_writes(controller) -> None:
    ctrl, backend = controller
    result = ctrl.run_action(app_module.MATRIX_SELF_TEST_ACTION)
    assert result["ok"] is True
    report = result["self_test"]
    assert report and report[0].startswith("MATRIX SELF-TEST")
    joined = "\n".join(report)
    # The G11 empirical facts are in the report: CapsLock scanned at slot 58.
    caps_lines = [line for line in report if "Caps Lock" in line and "code 0x" in line]
    assert caps_lines and "slot  58" in caps_lines[0] and "0xFF" in caps_lines[0]
    assert "as mapped" in caps_lines[0]
    assert "CapsLock = slot 58" in joined
    assert "slots 55-57, 59, 60 bound to a cap: NO" in joined
    assert "55, 56, 57, 59" in joined
    assert "Caps bound: 82" in joined
    # READ-ONLY contract: the only device traffic is the config-mirror GET.
    assert backend.calls_named("read_keymap")
    for writer in ("set_keymap_codes", "reset_keymap", "change_profile"):
        assert not backend.calls_named(writer), writer
    assert result["message"] == (
        "Matrix self-test complete — read-only scan, no writes sent."
    )


def test_matrix_self_test_flags_deviations_and_dead_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend(codes=[
        0x59 if slot == 58 else (0xFF if slot == 105 else FACTORY[slot])
        for slot in range(KEYMAP_SLOTS)
    ])
    ctrl, _ = make_controller(monkeypatch, backend)
    report = ctrl.run_action(app_module.MATRIX_SELF_TEST_ACTION)["self_test"]
    lines = "\n".join(report)
    caps_line = next(
        line for line in report if "Caps Lock" in line and "code 0x" in line
    )
    # CapsLock scans at the empirical cell 58; 0x59 there is a deviation
    # against the vendor byte (0xFF) — the scan stays byte-honest, G11.
    assert "slot  58" in caps_line
    assert "0x59" in caps_line and "DEVIATES (factory 0xFF)" in caps_line
    space_line = next(line for line in report if "Space" in line)
    assert "UNASSIGNED (0xFF) — keycap dead (factory 0x2C)" in space_line
    assert "DEVIATES" in lines


def test_matrix_self_test_blank_mirror_degrades_to_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeBackend()

    def blank_read_keymap(profile=1, layer=1):
        backend._note("read_keymap", profile, layer)
        raise BackendError(BLANK_MIRROR_BACKEND_ERROR)

    ctrl, backend = make_controller(monkeypatch, backend)
    monkeypatch.setattr(backend, "read_keymap", blank_read_keymap)
    result = ctrl.run_action(app_module.MATRIX_SELF_TEST_ACTION)
    assert result["ok"] is True
    joined = "\n".join(result["self_test"])
    assert "packaged factory defaults" in joined
    assert "NOTE: Config mirror is blank" in joined
    caps_line = next(
        l for l in result["self_test"] if "Caps Lock" in l and "code 0x" in l
    )
    assert "as mapped" in caps_line
    for writer in ("set_keymap_codes", "reset_keymap", "change_profile"):
        assert not backend.calls_named(writer), writer


def test_matrix_self_test_all_zero_mirror_degrades_to_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, _ = make_controller(monkeypatch, FakeBackend(codes=[0x00] * KEYMAP_SLOTS))
    result = ctrl.run_action(app_module.MATRIX_SELF_TEST_ACTION)
    assert result["ok"] is True
    joined = "\n".join(result["self_test"])
    assert "all-zero (shadow buffer)" in joined
    assert "packaged factory defaults" in joined


def test_matrix_self_test_without_device_is_honest_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctrl, _ = make_controller(monkeypatch, FakeBackend(available=False))
    result = ctrl.run_action(app_module.MATRIX_SELF_TEST_ACTION)
    assert result["ok"] is False
    assert result["self_test"] is None
    assert "Fake install hint." in result["message"]


def test_matrix_self_test_non_blank_failure_stays_ok_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # G8 discipline: ONLY the blank-mirror signature degrades.
    backend = FakeBackend()

    def gone_read_keymap(profile=1, layer=1):
        backend._note("read_keymap", profile, layer)
        raise BackendError("device vanished mid-scan")

    ctrl, backend = make_controller(monkeypatch, backend)
    monkeypatch.setattr(backend, "read_keymap", gone_read_keymap)
    result = ctrl.run_action(app_module.MATRIX_SELF_TEST_ACTION)
    assert result["ok"] is False
    assert result["self_test"] is None
    assert "device vanished mid-scan" in result["message"]


def test_cli_flag_prints_report_and_exits_before_gui(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class CliBackend(FakeBackend):
        # main() builds its own controller, so the test cannot wire
        # backend.lock; record calls without the lock assertion.
        def _note(self, name: str, *args) -> None:
            self.calls.append((name, *args))

    backend = CliBackend()
    monkeypatch.setattr(app_module, "create_backend", lambda: backend)
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [DEVICE])

    def no_server(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("--self-test-matrix must not start the server")

    monkeypatch.setattr(app_module, "ThreadingHTTPServer", no_server)
    monkeypatch.setattr("sys.argv", ["app.py", app_module.MATRIX_SELF_TEST_FLAG])
    with pytest.raises(SystemExit) as exitinfo:
        app_module.main()
    assert exitinfo.value.code == 0
    out = capsys.readouterr().out
    assert "MATRIX SELF-TEST" in out
    assert "Caps Lock" in out
    assert "slots 55-57, 59, 60 bound to a cap: NO" in out
    assert not backend.calls_named("set_keymap_codes")
