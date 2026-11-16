"""Headless tests for the PURE per-key helpers in static/app.js (G19A commit 2).

The G19A plan pins four JS guarantees, all extracted verbatim between the
``[PURE-PERKEY-START]`` / ``[PURE-PERKEY-END]`` markers (plus the existing
``[PURE-HEX]`` section they format through) and run under bare node:

* ONE canonical visibility map: off/lit/breath -> bytes 0x00/0xFF/0xFE, with
  the hex LABELS formatted from those numbers via ``keycodeHex`` — the G18 F1
  string-radix bug class must be structurally impossible here.
* The "N keys colored" status chip counts saved slots.
* The dedicated /api/per-key/* (and /api/win-lock) route strings.
* Edit-mode gating: the keycap click opens the color panel ONLY when per-key
  mode is enabled AND Edit mode is on — otherwise the remap drawer behavior
  is unchanged, and the two destinations are mutually exclusive.

Plus a DOM-free source check: the pinned UI strings exist and no wording
anywhere claims a device color readback (none exists on this board).

G19A-FIX1 (static front-end polish) extends the same JS-extraction pattern:
the win-lock status map, the canonical slot-tag / strip-label / picker-
subtitle functions, the Remove-all confirm gate, the picker handle clamp
(new ``[PURE-PICKER]`` section) and the renamed button labels are all pinned
DOM-free here.

Node binary: $OGC_NODE, then ``node`` on PATH, then the pinned harness path —
when none exists the node tests skip (same pattern as test_keymap_hex_js.py).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "open_glorious_core" / "static"
APP_JS = STATIC / "app.js"
INDEX_HTML = STATIC / "index.html"
STYLES_CSS = STATIC / "styles.css"
PINNED_NODE = "/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node"

HEX_START, HEX_END = "// [PURE-HEX-START]", "// [PURE-HEX-END]"
PK_START, PK_END = "// [PURE-PERKEY-START]", "// [PURE-PERKEY-END]"
PICK_START, PICK_END = "// [PURE-PICKER-START]", "// [PURE-PICKER-END]"


def _node() -> str:
    for candidate in (os.environ.get("OGC_NODE"), shutil.which("node"), PINNED_NODE):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    pytest.skip("no node binary available (set $OGC_NODE)")


def _section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    stop = text.index(end)
    assert begin < stop, f"markers out of order: {start}"
    section = text[begin:stop]
    assert "document" not in section
    assert "window" not in section
    return section


def _pure_bundle() -> str:
    text = APP_JS.read_text(encoding="utf-8")
    # PURE-PERKEY formats labels through the shared keycodeHex, so the bundle
    # is PURE-HEX followed by PURE-PERKEY (declaration order, verbatim).
    return _section(text, HEX_START, HEX_END) + _section(text, PK_START, PK_END)


def _eval(expr: str):
    script = _pure_bundle() + f"\nconsole.log(JSON.stringify({expr}));\n"
    result = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _perkey_section_text() -> str:
    return _section(APP_JS.read_text(encoding="utf-8"), PK_START, PK_END)


def _eval_picker(expr: str):
    # The PURE-PICKER handle math is standalone (no shared bundle needed).
    text = APP_JS.read_text(encoding="utf-8")
    script = _section(text, PICK_START, PICK_END) + f"\nconsole.log(JSON.stringify({expr}));\n"
    result = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --- the one canonical vis map --------------------------------------------------

@pytest.mark.parametrize(
    ("vis", "byte", "label"),
    [
        ("off", 0x00, "0x00"),
        ("lit", 0xFF, "0xFF"),
        ("breath", 0xFE, "0xFE"),
    ],
)
def test_vis_map_bytes_and_labels(vis: str, byte: int, label: str) -> None:
    assert _eval(f"perKeyVisByte({json.dumps(vis)})") == byte
    assert _eval(f"perKeyVisLabel({json.dumps(vis)})") == label


def test_vis_map_is_canonical_and_radix_safe() -> None:
    section = _perkey_section_text()
    # The three vendor bytes are literals EXACTLY ONCE in the whole section.
    for literal in ("0x00", "0xFF", "0xFE"):
        assert section.count(literal) == 1, f"{literal} must appear once (canonical map)"
    # No string radix parsing anywhere in the per-key section.
    assert "parseInt" not in section
    assert 'toString(16)' not in section  # labels format through keycodeHex
    # Unknown vis yields null / em-dash — never a silent 0.
    assert _eval('perKeyVisByte("on")') is None
    assert _eval('perKeyVisByte("0xFE")') is None
    assert _eval('perKeyVisLabel("glow")') == "—"


def test_vis_names_render_for_the_strip() -> None:
    assert _eval('perKeyVisName("off")') == "OFF"
    assert _eval('perKeyVisName("lit")') == "Lit"
    assert _eval('perKeyVisName("breath")') == "Breathing"
    assert _eval('perKeyVisName("pulse")') == "—"


# --- the "N keys colored" chip ---------------------------------------------------

@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        ({}, "0 keys colored"),
        (None, "0 keys colored"),
        ({"0": {"vis": "lit", "rgb": [255, 255, 255]}}, "1 keys colored"),
        (
            {
                "0": {"vis": "lit", "rgb": [255, 0, 0]},
                "15": {"vis": "breath", "rgb": [0, 255, 0]},  # side LED slot
                "119": {"vis": "off", "rgb": [0, 0, 255]},  # OFF still counts
            },
            "3 keys colored",
        ),
    ],
)
def test_colored_count_chip(slots, expected: str) -> None:
    assert _eval(f"perKeyChipText({json.dumps(slots)})") == expected


# --- API route strings ------------------------------------------------------------

def test_dedicated_route_strings_present_and_exact() -> None:
    routes = _eval("PER_KEY_ROUTES")
    assert routes == {
        "winLock": "/api/win-lock",
        "enable": "/api/per-key/enable",
        "brightness": "/api/per-key/brightness",
        "color": "/api/per-key/color",
        "clear": "/api/per-key/clear",
    }
    source = APP_JS.read_text(encoding="utf-8")
    for path in routes.values():
        assert f'"{path}"' in source, f"route {path} missing from app.js"


# --- edit-mode gating ---------------------------------------------------------------

@pytest.mark.parametrize("enabled", [False, True, None, 1, 0])
@pytest.mark.parametrize("edit", [False, True, None, 1, 0])
def test_gating_table_color_panel_xor_drawer(enabled, edit) -> None:
    opens_panel = _eval(
        f"perKeyClickOpensColorPanel({json.dumps(enabled)}, {json.dumps(edit)})"
    )
    opens_drawer = _eval(
        f"perKeyClickOpensDrawer({json.dumps(enabled)}, {json.dumps(edit)})"
    )
    expected_panel = bool(enabled) and bool(edit)
    assert opens_panel is expected_panel
    # MUTUALLY EXCLUSIVE — never both, never neither.
    assert opens_panel is not opens_drawer


def test_disabled_per_key_never_owns_the_click() -> None:
    # The pinned rule: with per-key OFF the grid keeps the drawer click even
    # if the (disabled) Edit switch were somehow on.
    assert _eval("perKeyClickOpensColorPanel(false, true)") is False
    assert _eval("perKeyClickOpensDrawer(false, true)") is True


# --- keycap bottom-face tint ----------------------------------------------------------

def test_tint_lights_lit_and_breath_but_not_off_or_garbage() -> None:
    per_key = {
        "enabled": True,
        "brightness": 100,
        "slots": {
            "0": {"vis": "lit", "rgb": [255, 0, 0]},
            "3": {"vis": "breath", "rgb": [0, 0, 255]},
            "5": {"vis": "off", "rgb": [0, 255, 0]},
            "7": {"vis": "lit", "rgb": [300, 0, 0]},
            "9": {"vis": "lit", "rgb": [0, 1]},
        },
    }
    expr = f"[0,3,5,7,9,11].map((slot) => perKeyTintRgb({json.dumps(per_key)}, slot))"
    tints = _eval(expr)
    assert tints[0] == [255, 0, 0]
    assert tints[1] == [0, 0, 255]
    assert tints[2] is None  # OFF slot shows no tint
    assert tints[3] is None  # out-of-range channel
    assert tints[4] is None  # not a triple
    assert tints[5] is None  # absent slot


def test_tint_requires_enabled_mode() -> None:
    slots = {"0": {"vis": "lit", "rgb": [1, 2, 3]}}
    assert _eval(f"perKeyTintRgb({json.dumps({'enabled': False, 'slots': slots})}, 0)") is None
    assert _eval("perKeyTintRgb(null, 0)") is None


# --- pinned UI strings, DOM-free --------------------------------------------------------

def test_win_lock_strings_are_pinned_in_html() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert "Windows Key Lock" in html
    # Subtitle verbatim (G19A-FIX1 D1 plan-pinned) — a MUTED subtitle under
    # the LEFT label, not a bold duplicate title beside the toggle.
    assert "Blocks the Windows key — takes effect on Apply; support varies by firmware" in html
    assert "<strong>Windows Key Lock</strong>" not in html
    assert 'id="win-lock-toggle"' in html
    # Short inline status text in the toggle column (side-lights pattern),
    # defaulting to the map's UNLOCKED string.
    assert 'id="win-lock-status"' in html
    assert ">Windows key unlocked<" in html
    assert "Windows key locked" not in html  # painted by JS, never static
    # G19A-FIX1 D1: BOTH performance toggles share one fixed right-edge
    # column, so the switches never drift apart.
    assert html.count('class="toggle performance-toggle"') == 2


def test_per_key_ui_strings_are_pinned_in_html() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    for needle in (
        "Per-key colors",
        "Paint every key white",
        "Remove all colors",
        "Edit mode",
        "Turn key OFF",
        "Breathing",
        'id="per-key-brightness"',
        'min="0" max="100"',
    ):
        assert needle in html, needle
    # Honest phrasing per the plan: stored locally, never a device read.
    assert "stored in your profile" in html.lower()
    # G19A-FIX1 D3/D4: the old destructive-vague labels are GONE.
    for gone in ("Reset all keys", "Clear all", "Set OFF", "Clear color"):
        assert gone not in html, gone


@pytest.mark.parametrize("needle", [
    "reads the colors",
    "read the colors",
    "read back from the keyboard",
    "read back from the device",
    "current colors on the keyboard",
])
def test_no_readback_claim_anywhere_in_static_ui(needle: str) -> None:
    for path in (INDEX_HTML, APP_JS):
        text = path.read_text(encoding="utf-8").lower()
        assert needle not in text, f"{path.name} claims a readback: {needle!r}"


def test_reuse_not_duplicate_the_slot_table() -> None:
    # The per-key grid IS the keymap grid: no second slot table may be
    # introduced for per-key (KEYMAP_SLOT_TABLE stays the single source).
    source = APP_JS.read_text(encoding="utf-8")
    assert len(re.findall(r"KEYMAP_TABLE\s*=", source)) == 1
    assert "PER_KEY_SLOT_TABLE" not in source
    assert "per-key-grid" not in source


# --- G19A-FIX1: win-lock status map (D1) ------------------------------------------

def test_win_lock_status_map_round_trip() -> None:
    assert _eval("winLockStatusText(true)") == "Windows key locked"
    assert _eval("winLockStatusText(false)") == "Windows key unlocked"


def test_win_lock_status_map_is_the_only_status_source() -> None:
    section = _perkey_section_text()
    # The two strings exist EXACTLY ONCE across the whole app.js — inside
    # this map — and the renderer paints the row through the function.
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count('"Windows key locked"') == 1
    assert source.count('"Windows key unlocked"') == 1
    assert '"Windows key locked"' in section
    assert '"Windows key unlocked"' in section
    renderer = source.split("function renderPerformance", 1)[1].split("\n}\n", 1)[0]
    assert "winLockStatusText(state.winLock)" in renderer


# --- G19A-FIX1: canonical slot tag / strip label / picker subtitle (D2) ------------

def test_slot_subtitle_function_swaps_and_restores() -> None:
    original = "This effect supports a single custom color."
    # Strip active -> 'Editing the color of Slot N' with the NUMERIC slot.
    assert _eval(f'perKeyPickerSubtitle(true, 7, {json.dumps(original)})') == "Editing the color of Slot 7"
    assert _eval(f'perKeyPickerSubtitle(true, "42", {json.dumps(original)})') == "Editing the color of Slot 42"
    # Strip inactive -> the original effect subtitle restored verbatim.
    assert _eval(f'perKeyPickerSubtitle(false, 7, {json.dumps(original)})') == original
    assert _eval('perKeyPickerSubtitle(false, null, "x")') == "x"


def test_strip_label_and_subtitle_share_one_slot_tag() -> None:
    # ONE canonical tag ("Slot " + number): the strip label and the picker
    # subtitle can never show different slot numbers.
    assert _eval("perKeySlotTag(7)") == "Slot 7"
    assert _eval('perKeyStripLabel(7, null)').startswith("Slot 7 · ")
    assert _eval('perKeyStripLabel("12", {"vis": "breath", "rgb": [1, 2, 3]})') == "Slot 12 · Breathing (0xFE)"
    tag = _eval("perKeySlotTag(23)")
    assert _eval('perKeyPickerSubtitle(true, 23, "")') == f"Editing the color of {tag}"
    # Structural: the `Slot ${` template is written exactly ONCE in the
    # whole pure section — no duplicated slot math (G18 radix lesson).
    assert _perkey_section_text().count("`Slot ${") == 1
    assert "Number(slot)" in _perkey_section_text()


# --- G19A-FIX1: Remove-all confirm gate (D4) ----------------------------------------

def test_remove_all_confirm_gates_the_api_call() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    handler = (
        source.split('els.perKeyClearAll.addEventListener("click"', 1)[1]
        .split("\n  });", 1)[0]
    )
    # confirm() fires BEFORE the bulk path (runPerKeyBulk -> postPerKey ->
    # fetch); a cancel returns silently — no fetch, no state change.
    assert "window.confirm(" in handler
    assert "runPerKeyBulk(" in handler
    assert handler.index("window.confirm(") < handler.index("runPerKeyBulk(")
    assert "Remove all ${slots.length} stored key colors?" in handler
    first_confirm_line = handler.split("window.confirm(", 1)[1].split("\n", 1)[0]
    assert "return;" in first_confirm_line  # `if (!confirm(...)) return;`
    # The Paint-all path is NOT gated (it is not destructive-by-deletion).
    reset_handler = (
        source.split('els.perKeyResetAll.addEventListener("click"', 1)[1]
        .split("\n  });", 1)[0]
    )
    assert "window.confirm(" not in reset_handler


# --- G19A-FIX1: picker handle clamp math (D5) ----------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-50, 9),  # below the top/left corner -> inset by the radius
        (0, 9),
        (9, 9),  # exactly at r stays
        (150, 150),  # interior untouched
        (291, 291),  # exactly at dim - r stays
        (300, 291),  # beyond the bottom/right corner -> inset
        (9999, 291),
    ],
)
def test_handle_clamp_bounds(raw: float, expected: float) -> None:
    # 300px surface, 18px handle -> radius 9, bounds [9, 300-9].
    assert _eval_picker(f"pickerHandleOffset({raw}, 300, 9)") == expected


def test_handle_clamp_never_inverts_on_tiny_surfaces() -> None:
    # dim < 2r must not produce max < min: the clamp stays at r.
    assert _eval_picker("pickerHandleOffset(4, 8, 9)") == 9


def test_renderer_clamps_handle_pixels_and_css_keeps_circle_visible() -> None:
    source = APP_JS.read_text(encoding="utf-8")
    painter = source.split("function renderPicker", 1)[1].split("\n}\n", 1)[0]
    assert "pickerHandleOffset(state.picker.saturation * rect.width, rect.width, radius)" in painter
    assert "pickerHandleOffset((1 - state.picker.value) * rect.height, rect.height, radius)" in painter
    # CSS: the handle layer must not clip — overflow stays visible on the
    # surface so the FULL circle shows at max-saturation/high-value corners.
    css = STYLES_CSS.read_text(encoding="utf-8")
    surface_block = css.split(".color-surface {", 1)[1].split("}", 1)[0]
    assert "overflow: visible" in surface_block


# --- G19A-FIX1: renamed labels + tooltips + chip/note (D3/D4/D6/D7) ------------------

@pytest.mark.parametrize(
    "label",
    ["Turn key OFF", "Remove color", "Paint every key white", "Remove all colors"],
)
def test_renamed_labels_are_verbatim_in_html(label: str) -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert label in html


def test_strip_tooltips_and_action_note_are_pinned() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'title="Keep the color but mark the key dark (0x00)"' in html
    assert 'title="Delete this slot from your per-key layout"' in html
    assert "Paint every key white to reset; remove colors individually or all at once." in html


def test_chip_and_gap_css_are_pinned() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    # D6: the counter is a real chip (background + border + padding + a
    # right margin clearing the scrollbar gutter).
    chip_block = css.split(".per-key-chip {", 1)[1].split("}", 1)[0]
    for needle in ("background", "border: 1px", "padding:", "margin-right:"):
        assert needle in chip_block, needle
    # D4: muted one-liner style exists.
    assert ".per-key-actions-note" in css
    # D1: both performance toggles share the fixed right-edge column.
    assert ".performance-row .toggle.performance-toggle" in css
    # D7: the CUSTOM COLOR head no longer double-seats its gap.
    panel_block = css.split(".custom-color-panel {", 1)[1].split("}", 1)[0]
    assert "margin-top: 0" in panel_block and "padding-top: 0" in panel_block
