"""G10 — headless node tests for static/keymap-slots.js (the cap→slot table).

The table module is DOM-free by contract (loaded as a classic script, required
by node in tests). This harness runs it under the pinned node binary and pins
the operator-incident facts: the cap labeled "Caps Lock" resolves to slot 60,
spare slot 58 is bound to nothing, and the clickable set is exactly the 82
physical keycaps. No Qt, no browser, no device I/O.
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
    assert _eval("t.rows[3].slots[0]") == 60  # row 4 starts at real CapsLock slot
    assert _eval("t.rows[3].keys[0]") == "Caps Lock"


def test_caps_lock_cap_resolves_to_slot_60() -> None:
    assert _eval('t.slotForCapLabel("Caps Lock")') == 60
    # The G10 incident: the "Caps Lock" cap wrote slot 58. 58 must resolve to
    # NO cap and live in NO visual row.
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
    assert 60 in clickable
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
