"""G12 item 5 + G15 — structural E2E: replay the FULL GUI remap flow against
the fake HID double, and pin what the wire must carry.

The GUI flow (tests/conftest.FakeHIDDevice models the live WB5092 board: the
GET mirror stays blank, so the panel degrades to factory defaults with the
banner):

  open drawer (GET keystate) → click the Caps Lock cap (slot 58, G11) →
  set target NumpadDivide (0x54, the operator's incident write) →
  Apply (POST apply-keymap) → arm verify → one physical keystroke
  ("NumpadDivide") → VERIFIED.

Pins (G15 is the point): the captured cmd-0x03 report carries
codes[58] == 0x54 (NOT 60 — the vendor table's Caps Lock cell, physically
dead on this board) RIDING types[58] == 0x01 — the EFFECTIVE types overlay,
not the packaged vendor table (whose 0xFF at 58 made the operator's write a
silent no-op: the firmware honors the TYPE byte as cell activation).
Everything else on the types wire is the packaged table verbatim: the knob
keeps its 0x03 consumer typing, the modifier set keeps 0x02. "Reset to
factory" applies the EMPIRICAL overlay (codes[58]=0x39, types[58]=0x01 —
never 0xFF) so a factory reset keeps the caps keycap alive. The last-good
store rides the same flow: apply → pending, keystroke → confirmed revert
target.

Zero device I/O (conftest guards the real IO boundary; the double is wired at
that boundary on purpose).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from open_glorious_core import app as app_module
from open_glorious_core import lastgood
from open_glorious_core.backend import (
    KEYMAP_CODES_OFFSET,
    KEYMAP_SLOTS,
    KEYMAP_TYPES_OFFSET,
    StockGmmkProBackend,
)
from open_glorious_core.device import HidDevice
from open_glorious_core.app import VERIFY_ARM_BANNER, build_effective_types

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "open_glorious_core" / "data"
FACTORY = (DATA / "default_keymap.bin").read_bytes()
DEFAULT_TYPES = (DATA / "default_types.bin").read_bytes()

DEVICE = HidDevice(
    hidraw="/dev/hidraw-test",
    name="GMMK Pro",
    vendor_id=0x218B,
    product_id=0x0A2B,
    physical_id="test-1",
    interface_id=2,
)


def _build(fake_hid, monkeypatch: pytest.MonkeyPatch):
    """Controller over the REAL StockGmmkProBackend wired to the fake HID."""
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    monkeypatch.setattr(app_module, "create_backend", lambda: backend)
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [DEVICE])
    controller = app_module.OpenGloriousCoreWebController()
    return controller, backend


def _codes_of(report: bytes) -> bytes:
    return report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS]


def _types_of(report: bytes) -> bytes:
    return report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS]


# ---------------------------------------------------------------------------
# The full remap flow
# ---------------------------------------------------------------------------


def test_full_gui_flow_remaps_caps_slot_58_and_verifies(
    fake_hid, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = fake_hid
    ctrl, _ = _build(device, monkeypatch)

    # 1. Operator opens the drawer: GET /api/keystate. The blank mirror
    #    degrades to factory defaults — and the EMPIRICAL overlay shows the
    #    caps cell honestly (G12 item 6), the row-4 keycap labelled
    #    "Caps Lock (0x39) · normal".
    keystate = ctrl.get_keystate()
    assert keystate["ok"] is True
    slots = keystate["keymap"]["slots"]
    assert slots[58]["code"] == 0x39
    assert slots[58]["code_name"] == "Caps Lock (0x39)"
    assert slots[58]["code_type_label"] == "normal"
    # G15 item 2: the panel reports the EFFECTIVE types (type_name +
    # cell_active), so 0xFF vendor bytes can no longer render the empirically
    # alive caps cell as an inert "unused" spare.
    assert slots[58]["type_name"] == "normal"
    assert slots[58]["cell_active"] is True
    assert slots[15]["type_name"] == "unused"
    assert slots[15]["cell_active"] is False
    baseline = [slot["code"] for slot in slots]  # applyKeystate() copies this

    # 2. Click the Caps Lock cap. The shipped slot table (keymap-slots.js,
    #    the GUI's cap→slot map) resolves the label to slot 58 — G11.
    table = app_module.load_slot_table()
    caps_slots = {
        slot
        for row in table["rows"]
        for slot, key in zip(row["slots"], row["keys"])
        if key == "Caps Lock"
    }
    assert caps_slots == {58}

    # 3. Pick the target in the editor drawer: NumpadDivide (0x54) — the
    #    operator's exact incident write.
    buffer = list(baseline)
    buffer[58] = 0x54
    assert buffer[60] == 0x39  # the vendor table's Caps Lock cell, untouched

    # 4. Apply → POST /api/action apply-keymap (the GUI sends codes only;
    #    the controller pairs them with the EFFECTIVE types overlay — G15).
    result = ctrl.run_action("apply-keymap", {"codes": buffer})
    assert result["ok"] is True, result["message"]

    # 5. The wire: exactly ONE full-matrix write, carrying the remap AND
    #    the types that make the firmware honor it.
    matrices = device.matrices
    assert len(matrices) == 1
    report = matrices[0]
    assert report[0] == 0x07 and report[2] == 1 and report[3] == 1  # id, profile, layer
    codes = _codes_of(report)
    types = _types_of(report)
    assert codes[58] == 0x54, "caps cell remapped — slot 58, NOT 60 (G11)"
    assert codes[60] == 0x39, "the dead slot-60 cell keeps its vendor byte"
    # THE G15 PIN: codes[58]=0x54 RIDES types[58]=0x01. The vendor default
    # (0xFF) makes the cell ignore its code — the root cause #2: the
    # operator's GUI write did nothing exactly because the types array was
    # the packaged vendor table.
    assert types[58] == 0x01, "assigned caps cell activates as a normal key"
    assert types[58] != 0xFF, "never ship the vendor 0xFF onto an assigned cell"
    # Rule 3 (protection): the knob consumer typing and the modifier set are
    # never touched by a normal apply.
    assert types[14] == 0x03, "knob keeps its consumer typing"
    for slot in (80, 91, 100, 101, 102, 108, 110):
        assert types[slot] == 0x02, f"modifier slot {slot} keeps 0x02"
    # The whole types array is exactly the effective overlay — the packaged
    # table with slot 58 forced active, no byte drift anywhere else.
    assert types == build_effective_types(bytes(buffer), DEFAULT_TYPES)
    expected = bytearray(DEFAULT_TYPES)
    expected[58] = 0x01
    assert types == bytes(expected)

    # 6. Item 1 + 4: the applied matrix is persisted as PENDING insurance —
    #    it is not the revert target until a keystroke confirms it.
    state = lastgood.load(1, 1)
    assert state is not None
    assert state["pending"] == buffer
    assert state["verified"] is False and state["seed"] is True

    # 7. The GUI arms the matcher with the first dirty slot + its target
    #    (writeKeymapBuffer's arm-after-apply).
    arm = ctrl.arm_verify(58, [buffer[58]])
    assert arm["ok"] is True
    assert arm["message"] == VERIFY_ARM_BANNER

    # 8. ONE physical keystroke: the remapped caps cell emits NumpadDivide.
    verdict = ctrl.record_key_event("NumpadDivide", "/")
    assert verdict["recorded"] is True
    assert verdict["verify"]["banner"] == "VERIFIED — / now emits Keypad / (0x54)"

    # The confirmed matrix is now the revertable last-good…
    promoted = lastgood.load(1, 1)
    assert promoted["codes"] == buffer
    assert promoted["verified"] is True and promoted["pending"] is None
    # …and the 60 s timer was cancelled: still exactly one write on the wire.
    assert len(device.matrices) == 1


def test_reset_to_factory_keeps_caps_alive_via_overlay(
    fake_hid, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = fake_hid
    ctrl, _ = _build(device, monkeypatch)

    result = ctrl.run_action("reset-keymap", None)
    assert result["ok"] is True, result["message"]

    matrices = device.matrices
    assert len(matrices) == 1
    report = matrices[-1]
    codes, types = _codes_of(report), _types_of(report)
    # The EMPIRICAL overlay, NOT the raw vendor table: a factory reset that
    # writes 0xFF@58 physically KILLS the caps key (G11 incident).
    assert codes[58] == 0x39, "reset keeps Caps Lock bound at the empirical slot"
    assert types[58] == 0x01, "…as a normal key, not reserved"
    for slot in range(KEYMAP_SLOTS):
        if slot != 58:
            assert codes[slot] == FACTORY[slot], slot
            assert types[slot] == DEFAULT_TYPES[slot], slot

    # The knob + modifier typings survive the factory overlay untouched.
    assert types[14] == 0x03, "knob consumer typing survives factory reset"
    for slot in (80, 91, 100, 101, 102, 108, 110):
        assert types[slot] == 0x02, f"modifier slot {slot} survives"

    # The drawer keeps telling the truth about the caps cell.
    slots = ctrl.get_keystate()["keymap"]["slots"]
    assert slots[58]["code_name"] == "Caps Lock (0x39)"
    assert slots[58]["code_type_label"] == "normal"
    assert slots[58]["type_name"] == "normal" and slots[58]["cell_active"] is True


def test_verify_arm_feeds_the_same_wired_stack(
    fake_hid, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No keystroke within the (injected, fast) timeout on a seed-state pair:
    the auto-revert must NOT write — the wire stays as the GUI left it."""
    device = fake_hid
    ctrl, _ = _build(device, monkeypatch)
    ctrl._verify_timeout = 0.05

    keystate = ctrl.get_keystate()
    buffer = [slot["code"] for slot in keystate["keymap"]["slots"]]
    buffer[58] = 0x59
    assert ctrl.run_action("apply-keymap", {"codes": buffer})["ok"] is True
    writes_after_apply = len(device.matrices)

    ctrl.arm_verify(58, [0x59])  # seed state: nothing operator-confirmed yet
    import time

    time.sleep(0.5)
    assert ctrl.verify_view()["state"] == "no-input-no-revert"
    assert len(device.matrices) == writes_after_apply, "seed state is never auto-written"
