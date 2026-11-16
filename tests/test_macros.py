"""G19B Commit 1 — macro backend encoding + store + API (pinned bytes).

Protocol facts pinned by G19B_PLAN.md (vendor CORE 2.1 sources — ground
truth, do NOT re-derive):

* Macro data = feature report 7, cmd 0x05, 264-byte buffer, [2]=MacroID,
  payload [8..255] (248 bytes) = 3-byte steps, MAX 82 (248 // 3); trailing
  zeros mark the end. Step: b0 = (delay >> 8) | (0x80 while keydown),
  b1 = delay & 0xFF (BIG-ENDIAN 16-bit ms), b2 = HID keycode. Semantics:
  emit the state CHANGE, THEN wait `delay` ms.
* Delay must be >= 1. The vendor clamps zero/negative deltas to 1 — THIS
  writer REJECTS delay < 1 instead (decision documented in backend.py and
  store.py: the store keeps the operator's authored timing exactly).
* Macro type table = cmd 0x04, 264 bytes, [2]=profile+1, [3]=layer+1 (same
  1-based convention as cmd 0x03), two bytes PER SLOT from [8] (big-endian
  u16): repeat 0 "No Repeat" -> 0x0001, 1 "Repeat While Holding" -> 0xFFFF,
  2 "Toggle" -> 0x0000; unassigned slots stay 0x0000. With NO macro-assigned
  key the vendor does not build the buffer at all — so we send NOTHING.
  (Plan cross-check: the commit-3 bullet writes repeat 0 as "01 00"; the
  plan's own GROUND-TRUTH protocol section pins big-endian 0x0001, i.e.
  `00 01`, and FF FF / 00 00 are order-symmetric — the ground-truth section
  wins; see the resolution comment at backend.MACRO_TYPE_WIRE_PAIRS.)
* Matrix: macro cell type 0x05 with code = MacroID; everything else per the
  proven G15 effective-type rules. Apply order: bind (0x01) -> matrix (0x03)
  -> 0x04 ONLY if a key carries a macro -> one 0x05 per stored macro in
  MacroID order -> profile re-assert, ONLY when macro reports went out (with
  zero macros the emitted sequence is byte-identical to HEAD — hard rule 3).
* NO macro readback exists (live evidence 2026-11-15; the GET mirror is the
  blank shadow). THE STORE IS THE TRUTH — no route, panel or message in this
  feature ever claims to read macros off the keyboard.

Zero device I/O: conftest hard-blocks real IO; FakeHIDDevice captures the
byte-exact reports at the IO boundary.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from open_glorious_core import app as app_module
from open_glorious_core import store
from open_glorious_core.backend import (
    CMD_MACRO_DATA,
    CMD_MACRO_TYPE_TABLE,
    MACRO_MAX_EVENTS,
    MACRO_PAYLOAD_BYTES,
    MACRO_REPORT_LENGTH,
    MACRO_TYPE_TABLE_BYTES_PER_SLOT,
    BackendError,
    StockGmmkProBackend,
    build_macro_events_bytes,
    build_macro_type_table_bytes,
)
from open_glorious_core.device import HidDevice

ROOT = Path(__file__).resolve().parents[1]
FACTORY = (ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin").read_bytes()
TYPES = (ROOT / "src" / "open_glorious_core" / "data" / "default_types.bin").read_bytes()

DEVICE = HidDevice(
    hidraw="/dev/hidraw-test",
    name="GMMK Pro",
    vendor_id=0x218B,
    product_id=0x0A2B,
    physical_id="test-1",
    interface_id=2,
)
SERIAL = DEVICE.physical_id

# A = 0x04, F = 0x09 (HID usage). Semantics: emit the change, THEN wait.
EVENTS3 = [
    {"down": True, "code": 0x04, "delay": 120},
    {"down": False, "code": 0x04, "delay": 5},
    {"down": True, "code": 0x09, "delay": 300},
]
# b0/b1 = big-endian delay, 0x80 set while keydown; b2 = code.
PINNED_STREAM = bytes.fromhex("807804") + bytes.fromhex("000504") + bytes.fromhex("812c09")
# NOTE: 300 ms = 0x012C -> b0 = 0x01|0x80 = 0x81, b1 = 0x2C, code 0x09 (F).


def _events(count: int) -> list[dict]:
    return [{"down": True, "code": 0x04, "delay": 10} for _ in range(count)]


def _macro(name="hello", repeat=1, events=None) -> dict:
    return {"name": name, "repeat": repeat, "events": events if events is not None else EVENTS3}


@pytest.fixture()
def wired(fake_hid):
    """Real StockGmmkProBackend over the FakeHIDDevice; capture starts clean."""
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    fake_hid.capture.clear()
    return backend, fake_hid


def _controller(fake_hid, monkeypatch: pytest.MonkeyPatch):
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    monkeypatch.setattr(app_module, "create_backend", lambda: backend)
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [DEVICE])
    return app_module.OpenGloriousCoreWebController(), backend, fake_hid


def _cmd_reports(fake, cmd: int) -> list[bytes]:
    return [r for r in fake.capture if r[1] == cmd]


def _serve(ctrl):
    Handler = app_module.AppRequestHandler

    def handler(*args, **kwargs):
        return Handler(*args, directory=str(app_module.STATIC_DIR), controller=ctrl, **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post_json(base: str, path: str, payload) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    request = urllib.request.Request(
        base + path, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {}


def _get_json(base: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base + path) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, {}


# --- cmd 0x05 event encoding (plan-pinned stream) ------------------------------

def test_event_stream_pinned_bytes():
    payload = build_macro_events_bytes(EVENTS3)
    # The encoder returns the raw 3-byte-per-event stream (248-byte zero
    # padding / end marker is the REPORT builder's job — pinned below).
    assert len(payload) == 3 * 3
    # A-down 120 ms: 120 = 0x0078 -> 80 78 04; A-up 5 ms: 00 05 04;
    # F-down 300 ms: 300 = 0x012C -> 81 2C 09.
    assert payload == bytes.fromhex("807804000504812c09")
    assert payload == PINNED_STREAM


def test_event_bounds_82_accepted_83_rejected():
    assert MACRO_MAX_EVENTS == 248 // 3 == 82
    assert len(build_macro_events_bytes(_events(82))) == 82 * 3 == 246
    with pytest.raises(BackendError):
        build_macro_events_bytes(_events(83))


def test_delay_zero_is_rejected_not_clamped():
    # Plan semantics: "the vendor clamps zero/negative deltas to 1" — THIS
    # writer REFUSES them (documented decision): delay must be >= 1 on our
    # path; the store keeps authored timing, never silently rewrites it.
    with pytest.raises(BackendError):
        build_macro_events_bytes([{"down": True, "code": 0x04, "delay": 0}])
    with pytest.raises(BackendError):
        build_macro_events_bytes([{"down": True, "code": 0x04, "delay": -5}])
    # Upper clamp 65535 is the wire maximum: accepted; one more rejected.
    build_macro_events_bytes([{"down": True, "code": 0x04, "delay": 65535}])
    with pytest.raises(BackendError):
        build_macro_events_bytes([{"down": True, "code": 0x04, "delay": 65536}])


def test_event_shape_rejects():
    # The wire encoder is strict about WIRE-invalid values; exact shape
    # (no unknown keys, no missing keys) is enforced at the API/store
    # boundary (validated there — see the 400-route tests below).
    for bad in (
        {"down": 1, "code": 0x04, "delay": 5},          # down must be bool
        {"down": True, "code": 0xFF, "delay": 5},       # 0xFF = unassigned
        {"down": True, "code": 0x04},                    # missing delay
        {"down": True, "code": True, "delay": 5},        # bool code
    ):
        with pytest.raises(BackendError):
            build_macro_events_bytes([bad])


# --- cmd 0x04 type table (plan-pinned vectors) ---------------------------------

def test_type_table_report_pinned_profile2_layer3_slots58_12(backend):
    report = backend._build_macro_type_table_report(2, 3, {58: 1, 12: 0})
    assert len(report) == MACRO_REPORT_LENGTH == 264
    assert bytes(report[:4]) == bytes([0x07, 0x04, 0x03, 0x04])  # profile+1, layer+1
    # Repeat 1 = 0xFFFF big-endian at slot 58 -> FF FF at [8+116..117].
    assert report[124] == 0xFF and report[125] == 0xFF
    # Repeat 0 = 0x0001 BIG-ENDIAN at slot 12 -> 00 01 at [8+24..25]
    # (ground-truth section wins over the commit-3 bullet's "01 00"; the
    # byte is exactly why the NONZERO-OFFSET check below lists 25, not 24).
    assert report[32] == 0x00 and report[33] == 0x01
    # Nonzero bytes: exactly the two pairs — table-RELATIVE offsets
    # 25 (slot 12 low byte), 116/117 (slot 58 pair) = absolute 33/124/125.
    nonzero = [i for i in range(8, 248) if report[i]]
    assert nonzero == [33, 124, 125]
    assert [i - 8 for i in nonzero] == [25, 116, 117]
    assert all(not report[i] for i in range(248, 264))


def test_type_table_repeat_modes_big_endian(backend):
    for repeat, pair in ((0, (0x00, 0x01)), (1, (0xFF, 0xFF)), (2, (0x00, 0x00))):
        report = backend._build_macro_type_table_report(1, 1, {7: repeat})
        lo = 8 + 7 * MACRO_TYPE_TABLE_BYTES_PER_SLOT
        hi = lo + 1
        assert (report[lo], report[hi]) == pair, repeat
        if pair != (0, 0):  # Toggle is 0x0000 — an all-zero table IS the pin
            assert not any(report[i] for i in range(8, 248) if i not in (lo, hi))


def test_type_table_empty_is_all_zero(backend):
    table = build_macro_type_table_bytes({})
    assert len(table) == 120 * MACRO_TYPE_TABLE_BYTES_PER_SLOT == 240
    assert table == bytes(240)


def test_type_table_rejects_bad_input(backend):
    with pytest.raises(BackendError):
        build_macro_type_table_bytes({5: 3})      # repeat not in 0..2
    with pytest.raises(BackendError):
        build_macro_type_table_bytes({120: 1})    # slot out of range
    with pytest.raises(BackendError):
        backend._build_macro_type_table_report(4, 1, {})  # profile 1..3 only


# --- cmd 0x05 report ------------------------------------------------------------

def test_macro_data_report_pinned(backend):
    report = backend._build_macro_data_report(3, EVENTS3)
    assert len(report) == 264
    assert bytes(report[:3]) == bytes([0x07, 0x05, 0x03])  # [2] = MacroID
    assert bytes(report[8:17]) == PINNED_STREAM             # exact 9 event bytes
    assert all(not report[i] for i in range(17, 264))       # zero padding
    with pytest.raises(BackendError):
        backend._build_macro_data_report(10, EVENTS3)       # MacroID 0..9


def test_write_helpers_send_one_report_each(wired):
    backend, fake = wired
    message = backend.write_macro_type_table(1, 1, {20: 1})
    assert len(fake.capture) == 1
    assert len(fake.capture[0]) == 264 and fake.capture[0][1] == CMD_MACRO_TYPE_TABLE
    assert "cmd 4" in message
    fake.capture.clear()
    message = backend.write_macro_data(3, EVENTS3)
    assert len(fake.capture) == 1
    assert fake.capture[0][1] == CMD_MACRO_DATA and fake.capture[0][2] == 3
    # No readback phrasing anywhere (store is truth).
    assert "cannot report" in message or "stored on this computer" in message


# --- store: shape, round trip, validation ---------------------------------------

def test_store_round_trip_macros_and_slots():
    store.record(SERIAL, 1, 1, settings={"macros": {"3": _macro()},
                                         "macro_slots": {"20": 3}})
    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block["settings"]["macros"]["3"]["events"] == EVENTS3
    assert block["settings"]["macro_slots"] == {"20": 3}
    # The export document carries BOTH keys (settings ride the export).
    exported = store.export_layer(SERIAL, 1, 1)
    assert exported["settings"]["macros"]["3"]["name"] == "hello"
    assert exported["settings"]["macro_slots"] == {"20": 3}


def test_store_rejects_invalid_macros():
    bad_macros = [
        {"name": "x", "repeat": 3, "events": EVENTS3},        # repeat > 2
        {"name": "x", "repeat": 1, "events": []},              # empty
        {"name": "x", "repeat": 1, "events": _events(83)},     # overflow
        {"name": "y" * 25, "repeat": 1, "events": EVENTS3},    # name > 24
        {"name": "x", "repeat": 1, "events": [
            {"down": True, "code": 0x04, "delay": 0}]},        # delay 0 REJECTED
    ]
    for bad in bad_macros:
        with pytest.raises(store.StoreError):
            store.validate_macro(bad, "macro 0")
    with pytest.raises(store.StoreError):
        store.validate_macros({"10": _macro()})   # MacroID 0..9
    with pytest.raises(store.StoreError):
        store.validate_macro_slots({"120": 0})    # slot 0..119
    with pytest.raises(store.StoreError):
        store.validate_macro_slots({"20": 10})    # MacroID 0..9


def test_store_pair_guard_slot_without_macro():
    with pytest.raises(store.StoreError):
        store.validate_macro_pair({}, {"20": 3})
    with pytest.raises(store.StoreError):
        store.record(SERIAL, 1, 1, settings={"macros": {}, "macro_slots": {"20": 3}})


# --- controller: GET /api/state exposure + HTTP routes ---------------------------

def test_http_state_exposes_macros_and_routes_write_store_only(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    server, base = _serve(controller)
    try:
        status, state = _get_json(base, "/api/state")
        assert status == 200
        assert state["macros"] == {} and state["macro_slots"] == {}

        status, result = _post_json(base, "/api/macro/save", {
            "id": 3, "name": "combo", "repeat": 1, "events": EVENTS3,
        })
        assert status == 200 and result["ok"] is True
        assert result["macros"]["3"]["name"] == "combo"

        status, result = _post_json(base, "/api/macro/assign", {"slot": 20, "id": 3})
        assert status == 200 and result["macro_slots"] == {"20": 3}

        status, state = _get_json(base, "/api/state")
        assert state["macros"]["3"]["repeat"] == 1
        assert state["macro_slots"] == {"20": 3}

        # NOTHING above touched the device (macros go out at Apply only).
        assert fake.capture == []

        status, result = _post_json(base, "/api/macro/assign", {"slot": 20, "id": None})
        assert status == 200 and result["macro_slots"] == {}
        status, result = _post_json(base, "/api/macro/unassign-slots-of", {"id": 3})
        assert status == 200 and result["ok"] is True

        status, result = _post_json(base, "/api/macro/delete", {"id": 3})
        assert status == 200 and result["macros"] == {}
        assert fake.capture == []

        status, _ = _post_json(base, "/api/macro/nope", {})
        assert status == 404
    finally:
        server.shutdown()


def test_http_route_validations_400_store_untouched(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    server, base = _serve(controller)
    catalog = set(app_module.all_assignable_codes())
    bad_code = next(code for code in range(0x100) if code not in catalog)
    try:
        for payload in (
            {"id": 10, "name": "x", "repeat": 1, "events": EVENTS3},        # MacroID
            {"id": -1, "name": "x", "repeat": 1, "events": EVENTS3},
            {"id": 0, "name": "y" * 25, "repeat": 1, "events": EVENTS3},    # name
            {"id": 0, "name": "", "repeat": 1, "events": EVENTS3},          # empty name
            {"id": 0, "name": "x", "repeat": 3, "events": EVENTS3},         # repeat
            {"id": 0, "name": "x", "repeat": 1, "events": []},              # empty
            {"id": 0, "name": "x", "repeat": 1, "events": _events(83)},     # 83 steps
            {"id": 0, "name": "x", "repeat": 1,
             "events": [{"down": True, "code": 0x04, "delay": 0}]},         # delay 0
            {"id": 0, "name": "x", "repeat": 1,
             "events": [{"down": True, "code": 0x04, "delay": 70000}]},     # delay > max
            {"id": 0, "name": "x", "repeat": 1,
             "events": [{"down": True, "code": bad_code, "delay": 5}]},     # unknown key
            {"id": 0, "name": "x", "repeat": 1,
             "events": [{"down": True, "code": 0x04, "delay": 5, "x": 1}]},  # extra key
            {"id": 0, "name": "x", "repeat": 1, "events": "nope"},          # not a list
        ):
            status, result = _post_json(base, "/api/macro/save", payload)
            assert status == 400 and result["ok"] is False, payload
        for payload in (
            {"slot": 120, "id": 0},      # slot range
            {"slot": -1, "id": 0},
            {"slot": 20, "id": 10},      # MacroID range
            {"slot": 20, "id": 7},       # no stored macro content
        ):
            status, result = _post_json(base, "/api/macro/assign", payload)
            assert status == 400 and result["ok"] is False, payload
        status, result = _post_json(base, "/api/macro/delete", {"id": 0})
        assert status == 400 and result["ok"] is False  # nothing stored
        # Every rejection left the store EMPTY and the wire silent.
        block = store.get_layer(store.load(), SERIAL, 1, 1)
        settings = (block or {}).get("settings", {})
        assert not settings.get("macros") and not settings.get("macro_slots")
        assert fake.capture == []
    finally:
        server.shutdown()


# --- apply staging: plan order, byte-identity, revert ----------------------------

def _apply(controller, codes):
    return controller.run_action("apply-keymap", {"codes": list(codes)})


def test_apply_zero_macros_is_head_shaped(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    result = _apply(controller, FACTORY)
    assert result["ok"] is True
    cmds = [r[1] for r in fake.capture]
    assert cmds == [0x01, 0x03]  # bind -> matrix; NO 0x04, NO 0x05, no re-assert
    assert _cmd_reports(fake, CMD_MACRO_TYPE_TABLE) == []
    assert _cmd_reports(fake, CMD_MACRO_DATA) == []


def test_apply_stages_macros_in_plan_order(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    assert controller.macro_save(
        {"id": 3, "name": "combo", "repeat": 1, "events": EVENTS3})["ok"]
    assert controller.macro_assign({"slot": 20, "id": 3})["ok"]
    fake.capture.clear()
    codes = bytearray(FACTORY)
    codes[20] = 0x04  # the USER's raw key stays in the store/last-good
    result = _apply(controller, codes)
    assert result["ok"] is True

    cmds = [r[1] for r in fake.capture]
    assert cmds == [0x01, 0x03, 0x04, 0x05, 0x01]  # bind, matrix, table, data, re-assert

    matrix = fake.matrices[-1]
    # Macro cell: type 0x05 at [8+20], code = MacroID 3 at [128+20].
    assert matrix[8 + 20] == 0x05
    assert matrix[128 + 20] == 0x03
    # EVERY other cell per the existing effective-type rules on the RAW codes.
    head_types = app_module.build_effective_types(bytes(codes), TYPES)
    wire_types = app_module.build_macro_wire_codes(bytes(codes), {"20": 3})
    for slot in range(120):
        if slot == 20:
            continue
        assert matrix[8 + slot] == head_types[slot], slot
        assert matrix[128 + slot] == wire_types[slot] == codes[slot], slot

    table = _cmd_reports(fake, CMD_MACRO_TYPE_TABLE)[-1]
    assert bytes(table[:4]) == bytes([0x07, 0x04, 0x02, 0x02])  # profile1/layer1 +1
    lo = 8 + 20 * 2
    assert (table[lo], table[lo + 1]) == (0xFF, 0xFF)  # repeat 1, big-endian
    assert not any(table[i] for i in range(8, 248) if i not in (lo, lo + 1))

    data = _cmd_reports(fake, CMD_MACRO_DATA)[-1]
    assert (data[1], data[2]) == (0x05, 0x03)  # cmd, MacroID 3 (UI label M4)
    assert bytes(data[8:17]) == PINNED_STREAM
    assert all(not data[i] for i in range(17, 264))

    # Bind + trailing re-assert both name the same pair (raw 1-based values).
    binds = _cmd_reports(fake, 0x01)
    assert bytes(binds[0][:4]) == bytes(binds[1][:4]) == bytes([0x07, 0x01, 0x01, 0x01])

    # Store kept the RAW code at slot 20 (the MacroID is wire-only).
    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block["keymap"]["20"] == 0x04
    assert block["settings"]["macro_slots"] == {"20": 3}


def test_apply_stored_unassigned_macro_still_sends_0x05_only(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    assert controller.macro_save(
        {"id": 0, "name": "loose", "repeat": 2, "events": EVENTS3})["ok"]
    fake.capture.clear()
    assert _apply(controller, FACTORY)["ok"]
    # No assignment -> NO 0x04; stored non-empty macro -> one 0x05 (+re-assert).
    cmds = [r[1] for r in fake.capture]
    assert cmds == [0x01, 0x03, 0x05, 0x01]
    data = _cmd_reports(fake, CMD_MACRO_DATA)[-1]
    assert data[2] == 0


def test_apply_after_delete_reverts_cells_and_silences_macros(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    assert controller.macro_save(
        {"id": 3, "name": "combo", "repeat": 1, "events": EVENTS3})["ok"]
    assert controller.macro_assign({"slot": 20, "id": 3})["ok"]
    assert _apply(controller, FACTORY)["ok"]
    fake.capture.clear()

    assert controller.macro_delete({"id": 3})["ok"]  # clears cells too
    fake.capture.clear()
    assert _apply(controller, FACTORY)["ok"]
    cmds = [r[1] for r in fake.capture]
    assert cmds == [0x01, 0x03]  # cells reverted, NO 0x04, NO 0x05
    matrix = fake.matrices[-1]
    head_types = app_module.build_effective_types(FACTORY, TYPES)
    assert bytes(matrix[8:128]) == head_types  # slot 20 back to the old rules
    assert matrix[128 + 20] == FACTORY[20]


def test_reset_keymap_clears_assignments_keeps_library(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    assert controller.macro_save(
        {"id": 3, "name": "combo", "repeat": 1, "events": EVENTS3})["ok"]
    assert controller.macro_assign({"slot": 20, "id": 3})["ok"]
    fake.capture.clear()
    assert controller.run_action("reset-keymap", None)["ok"]
    # Assignments gone from the store; library stays.
    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block["settings"]["macro_slots"] == {}
    assert "3" in block["settings"]["macros"]
    # Wire: no 0x04 (nothing assigned), stored macro still ships (0x05),
    # trailing re-assert because a macro report went out.
    cmds = [r[1] for r in fake.capture]
    assert cmds == [0x01, 0x03, 0x05, 0x01]
    matrix = fake.matrices[-1]
    assert 0x05 not in matrix[8:128]  # no macro-typed cells left


def test_mixed_matrix_untouched_vs_head(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    codes = bytearray(FACTORY)
    codes[10] = 0xE0  # a modifier cell
    codes[30] = 0x06  # a normal cell
    head_result = _apply(controller, codes)
    assert head_result["ok"] is True
    head_capture = list(fake.capture)
    fake.capture.clear()
    # Same matrix + one macro elsewhere: only the macro's own cells/rows change.
    assert controller.macro_save(
        {"id": 1, "name": "mix", "repeat": 0, "events": EVENTS3})["ok"]
    assert controller.macro_assign({"slot": 50, "id": 1})["ok"]
    assert _apply(controller, codes)["ok"]
    head_matrix = [r for r in head_capture if r[1] == 0x03][0]
    new_matrix = fake.matrices[-1]
    for slot in range(120):
        if slot == 50:
            continue
        assert new_matrix[8 + slot] == head_matrix[8 + slot], slot
        assert new_matrix[128 + slot] == head_matrix[128 + slot], slot
    assert new_matrix[8 + 50] == 0x05 and new_matrix[128 + 50] == 0x01
    # repeat 0 -> 00 01 (big-endian 0x0001) at slot 50 of the 0x04 table.
    table = _cmd_reports(fake, CMD_MACRO_TYPE_TABLE)[-1]
    lo = 8 + 50 * 2
    assert (table[lo], table[lo + 1]) == (0x00, 0x01)


def test_apply_all_three_repeat_modes_and_macro_id_order(fake_hid, monkeypatch):
    # Commit-3 deepening of the "repeat modes map" bullet: ONE apply in which
    # all three modes appear in the 0x04 table at their slot positions, and
    # every stored macro ships as its own 0x05 in MACROID ORDER (not save
    # order). Repeat 0 -> 00 01 = big-endian 0x0001; see MACRO_TYPE_WIRE_PAIRS
    # (plan ground-truth section beats its stray "01 00" bullet — decided).
    controller, _, fake = _controller(fake_hid, monkeypatch)
    # Deliberate save order 1, 2, 0 -> the 0x05 stream must be 0, 1, 2.
    assert controller.macro_save(
        {"id": 1, "name": "off", "repeat": 0, "events": EVENTS3})["ok"]
    assert controller.macro_save(
        {"id": 2, "name": "hold", "repeat": 1, "events": EVENTS3})["ok"]
    assert controller.macro_save(
        {"id": 0, "name": "tog", "repeat": 2, "events": EVENTS3})["ok"]
    assert controller.macro_assign({"slot": 20, "id": 1})["ok"]
    assert controller.macro_assign({"slot": 40, "id": 2})["ok"]
    assert controller.macro_assign({"slot": 60, "id": 0})["ok"]
    codes = bytearray(FACTORY)
    codes[10] = 0xE0  # Left Control: modifier cell, existing HEAD rules
    codes[30] = 0x06  # Z: normal cell
    fake.capture.clear()
    assert _apply(controller, codes)["ok"]

    cmds = [r[1] for r in fake.capture]
    assert cmds == [0x01, 0x03, 0x04, 0x05, 0x05, 0x05, 0x01]

    table = _cmd_reports(fake, CMD_MACRO_TYPE_TABLE)[-1]
    assert (table[8 + 20 * 2], table[8 + 20 * 2 + 1]) == (0x00, 0x01)  # repeat 0
    assert (table[8 + 40 * 2], table[8 + 40 * 2 + 1]) == (0xFF, 0xFF)  # repeat 1
    assert (table[8 + 60 * 2], table[8 + 60 * 2 + 1]) == (0x00, 0x00)  # repeat 2
    # Header [0..3] + the three FFs + the single 01 are the ONLY nonzero bytes
    # => EVERY OTHER slot is 00 00, byte-exactly.
    assert {i for i in range(MACRO_REPORT_LENGTH) if table[i]} == {0, 1, 2, 3, 49, 88, 89}

    # Macro cells: type 0x05, code = MacroID; every other cell = HEAD rules.
    matrix = fake.matrices[-1]
    for slot, macro_id in ((20, 1), (40, 2), (60, 0)):
        assert matrix[8 + slot] == 0x05
        assert matrix[128 + slot] == macro_id
    head_types = app_module.build_effective_types(bytes(codes), TYPES)
    wire_types = app_module.build_macro_wire_codes(
        bytes(codes), {"20": 1, "40": 2, "60": 0})
    for slot in range(120):
        if slot in (20, 40, 60):
            continue
        assert matrix[8 + slot] == head_types[slot], slot
        assert matrix[128 + slot] == wire_types[slot] == codes[slot], slot

    data = _cmd_reports(fake, CMD_MACRO_DATA)
    assert [r[2] for r in data] == [0, 1, 2]  # MacroID order, one report each
    for report in data:
        assert bytes(report[8:17]) == PINNED_STREAM
        assert all(not report[i] for i in range(17, MACRO_REPORT_LENGTH))


# --- keystate + import/export -----------------------------------------------------

def test_keystate_exposes_macro_cells(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    assert controller.macro_save(
        {"id": 3, "name": "combo", "repeat": 1, "events": EVENTS3})["ok"]
    assert controller.macro_assign({"slot": 20, "id": 3})["ok"]
    state = controller.get_keystate()
    assert state["ok"] is True
    slot20 = next(s for s in state["keymap"]["slots"] if s["slot"] == 20)
    assert slot20["macro_id"] == 3
    assert slot20["macro_label"] == "M4"          # UI label = MacroID + 1
    assert slot20["code_type"] == 0x05
    assert slot20["code_type_label"] == "macro"
    slot21 = next(s for s in state["keymap"]["slots"] if s["slot"] == 21)
    assert slot21["macro_id"] is None and slot21["macro_label"] is None
    assert fake.capture == []


def test_import_stages_macros_never_writes(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    result = controller.import_profile({
        "keymap": {"20": 0x04},
        "settings": {"macros": {"3": _macro(repeat=1)}, "macro_slots": {"20": 3}},
    })
    assert result["ok"] is True
    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block["settings"]["macros"]["3"]["name"] == "hello"
    assert block["settings"]["macro_slots"] == {"20": 3}
    assert fake.capture == []
    # Export round-trips BOTH keys.
    exported = controller.export_profile()
    text = json.dumps(exported)
    assert '"macros"' in text and '"macro_slots"' in text
    assert fake.capture == []


def test_import_rejects_bad_macros_store_untouched(fake_hid, monkeypatch):
    controller, _, fake = _controller(fake_hid, monkeypatch)
    catalog = set(app_module.all_assignable_codes())
    bad_code = next(code for code in range(0x100) if code not in catalog)
    for settings in (
        {"macros": {"0": _macro(repeat=5)}},                              # repeat
        {"macros": {"0": _macro(events=_events(83))}},                    # overflow
        {"macros": {"0": _macro(events=[{"down": True, "code": bad_code,
                                         "delay": 5}])}},                 # unknown code
        {"macros": {"0": _macro(events=[{"down": True, "code": 0x04,
                                         "delay": 0}])}},                 # delay 0
        {"macros": {"0": _macro()}, "macro_slots": {"20": 9}},            # dangling ref
    ):
        result = controller.import_profile({"settings": settings})
        assert result["ok"] is False, settings
        assert "Import rejected" in result["message"]
    block = store.get_layer(store.load(), SERIAL, 1, 1)
    settings = (block or {}).get("settings", {})
    assert not settings.get("macros") and not settings.get("macro_slots")
    assert fake.capture == []
