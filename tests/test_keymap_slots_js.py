"""G10/G17 — headless node tests for static/keymap-slots.js (the cap→slot table).

The table module is DOM-free by contract (loaded as a classic script, required
by node in tests). This harness runs it under the pinned node binary and pins
the sweep-confirmed facts: the cap labeled "Caps Lock" resolves to slot 60 (the
vendor table's own CapsLock cell, confirmed by the G13 cell sweep; G17 retracts
the G11 slot-58 claim), the hidden head cells 55-59 (dead spare 58 included)
are bound to nothing, and the clickable set is exactly the 82 physical
keycaps. No Qt, no browser, no device I/O.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SLOTS_JS = ROOT / "src" / "open_glorious_core" / "static" / "keymap-slots.js"
APP_JS = ROOT / "src" / "open_glorious_core" / "static" / "app.js"
INDEX_HTML = ROOT / "src" / "open_glorious_core" / "static" / "index.html"
PINNED_NODE = "/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node"

NON_KEYCAP = [14, 55, 56, 57, 58, 59, 72, 92, 103, 104, 106, 107, 111]


def _node() -> str:
    for candidate in (os.environ.get("OGC_NODE"), shutil.which("node"), PINNED_NODE):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    pytest.skip("no node binary available (set $OGC_NODE)")


def _eval(expr: str):
    script = (
        f"const t = require({json.dumps(str(SLOTS_JS))});\n"
        f"console.log(JSON.stringify({expr}));\n"
    )
    result = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_slot_table_module_is_dom_free() -> None:
    text = SLOTS_JS.read_text(encoding="utf-8")
    assert "document" not in text
    assert "window." not in text
    assert "module.exports" in text, "node export guard missing (tests import the table)"


def test_module_loads_under_node() -> None:
    assert _eval("t.rows.length") == 6
    assert _eval("t.rows[3].slots[0]") == 60  # row 4 starts at the CapsLock slot (G17)
    assert _eval("t.rows[3].keys[0]") == "Caps Lock"


def test_caps_lock_cap_resolves_to_sweep_confirmed_slot_60() -> None:
    assert _eval('t.slotForCapLabel("Caps Lock")') == 60
    # The G13 sweep verdict (G17): the CapsLock cell is slot 60, the vendor
    # table's own cell — caps press emitted 00 00 69, nothing from 58.
    assert _eval("t.allVisualSlots().indexOf(60) !== -1") is True
    assert _eval("t.capName(60)") == "Caps Lock"
    assert _eval("t.isNonKeycap(60)") is False
    # Slot 58 (dead matrix spare) resolves to NO cap and lives in no row.
    assert _eval("t.allVisualSlots().indexOf(58) === -1") is True
    assert _eval("t.capName(58)") is None
    assert _eval("t.isNonKeycap(58)") is True
    assert _eval("t.slotForCapLabel('Slot 58')") is None


def test_head_of_block_spares_excluded() -> None:
    visual = set(_eval("t.allVisualSlots()"))
    assert not visual & {55, 56, 57, 58, 59}
    assert _eval("t.non_keycap_slots") == NON_KEYCAP


@pytest.mark.parametrize(
    ("slot", "name"),
    [(13, "PrtSc"), (105, "Space"), (109, "Fn"), (112, "Left"),
     (113, "Down"), (114, "Right")],
)
def test_triple_confirmed_slots_in_node(slot: int, name: str) -> None:
    assert _eval(f"t.capName({slot})") == name


def test_clickable_set_is_82_unique_physical_caps() -> None:
    clickable = _eval("t.clickableSlots()")
    assert len(clickable) == len(set(clickable)) == 82, "no two caps share a slot"
    assert 60 in clickable, "the CapsLock cap (slot 60) must be clickable"
    for slot in NON_KEYCAP:
        assert slot not in clickable
    assert _eval("t.clickableSlots().slice().sort((a, b) => a - b)[0]") == 0
    # Every G9 NON_KEYCAP slot stays excluded, head-of-block included.
    assert _eval(
        "[" + ",".join(str(s) for s in NON_KEYCAP) + "]"
        ".every((s) => t.clickableSlots().indexOf(s) === -1)"
    ) is True


def test_index_html_loads_the_table_before_app_js() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    slots_at = html.index('src="./keymap-slots.js"')
    app_at = html.index('src="./app.js"')
    assert slots_at < app_at, "the slot table must load before app.js"


def test_app_js_binds_caps_from_the_table_not_a_stride_formula() -> None:
    app = APP_JS.read_text(encoding="utf-8")
    assert "KEYMAP_SLOT_TABLE" in app
    assert "row * KEYMAP_ROW_SLOTS" not in app, (
        "G10: the row*stride slot formula is the defect class; caps must bind "
        "through the KEYMAP_MAP.md row table"
    )
    assert "KEYMAP_TABLE.rows" in app


# --- G10 FIX 4: unassigned-slot drawer note (pure predicate via node) --------

DRAWER_START = "// [PURE-DRAWER-START]"
DRAWER_END = "// [PURE-DRAWER-END]"


def _eval_drawer(expr: str):
    text = APP_JS.read_text(encoding="utf-8")
    section = text.split(DRAWER_START, 1)[1].split(DRAWER_END, 1)[0]
    script = f"{section}\nconsole.log(JSON.stringify({expr}));\n"
    result = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_unassigned_predicate_flags_factory_0xff_slots() -> None:
    # Factory 0xFF = unassigned matrix slot per the VENDOR table: the note
    # replaces the editor.
    assert _eval_drawer("keycapSlotIsUnassigned({factory_code: 0xFF})") is True
    assert _eval_drawer("keycapSlotIsUnassigned({factory_code: 0x39})") is False
    assert _eval_drawer("keycapSlotIsUnassigned(null)") is False
    assert _eval_drawer("keycapSlotIsUnassigned({})") is False
    # G17: a real keycap overrides the vendor 0xFF — the predicate's override
    # path (slotHasKeycap === true) stays; Caps Lock itself now lives at slot
    # 60 with factory byte 0x39, so it is editable on the plain vendor byte.
    assert _eval_drawer("keycapSlotIsUnassigned({factory_code: 0xFF}, true)") is False
    # A keycap-less 0xFF cell keeps the honest unassigned note.
    assert _eval_drawer("keycapSlotIsUnassigned({factory_code: 0xFF}, false)") is True


def test_drawer_and_selftest_wiring_present() -> None:
    app = APP_JS.read_text(encoding="utf-8")
    html = INDEX_HTML.read_text(encoding="utf-8")
    # Drawer note wired into renderKeymapEditor (note shown, editor hidden);
    # G11: the empirical-keycap override is passed through slotHasKeycap.
    editor = app.split("function renderKeymapEditor()", 1)[1].split("\n}", 1)[0]
    assert "keycapSlotIsUnassigned(meta, slotHasKeycap)" in editor
    assert "const slotHasKeycap = meta" in editor
    assert "toggleHidden(km.editorNormal, unassigned)" in editor
    assert "if (unassigned) return;" in editor
    # Note element exists and is DEFAULT-hidden.
    assert 'id="keymap-editor-unassigned"' in html
    assert "keymap-unassigned-note is-hidden" in html
    # Self-test aid: button + report exist, report DEFAULT-hidden; the handler
    # fires ONLY from the click wiring (definition + one call site).
    assert 'id="keymap-self-test"' in html and "Run\n                                matrix self-test" in html
    assert 'id="keymap-selftest-report" class="keymap-selftest-report is-hidden"' in html
    assert app.count("runMatrixSelfTest(") == 2  # definition + click wiring
    assert 'km.selfTest.addEventListener("click", () => runMatrixSelfTest());' in app
    assert "postAction(\"matrix-self-test\")" in app
    assert app.count('id="keymap-editor-normal"') == 0  # id lives in index.html
    assert 'id="keymap-editor-normal"' in html
