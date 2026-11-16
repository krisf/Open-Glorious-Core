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
PINNED_NODE = "/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node"

HEX_START, HEX_END = "// [PURE-HEX-START]", "// [PURE-HEX-END]"
PK_START, PK_END = "// [PURE-PERKEY-START]", "// [PURE-PERKEY-END]"


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
    # Subtitle verbatim (plan-pinned).
    assert "Blocks the Windows key (requires Apply; support varies by firmware)." in html
    assert 'id="win-lock-toggle"' in html


def test_per_key_ui_strings_are_pinned_in_html() -> None:
    html = INDEX_HTML.read_text(encoding="utf-8")
    for needle in (
        "Per-key colors",
        "Reset all keys",
        "Clear all",
        "Edit mode",
        "Set OFF",
        "Breathing",
        'id="per-key-brightness"',
        'min="0" max="100"',
    ):
        assert needle in html, needle
    # Honest phrasing per the plan: stored locally, never a device read.
    assert "stored in your profile" in html.lower()


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
