"""Headless tests for the PURE hex/label helpers in static/app.js (G18 F1/F2).

G18 factors two DOM-free helpers into a section between the
``[PURE-HEX-START]`` / ``[PURE-HEX-END]`` markers of
``src/open_glorious_core/static/app.js``:

* ``keycodeHex(code)`` — G18 F1 regression. The old inline
  ``String(code).toString(16)`` called ``toString(16)`` on a STRING, which
  IGNORES the radix and returned the decimal text verbatim (10 -> "0x10"
  instead of "0x0A", 29 -> "0x1D" broken the same way). The helper converts
  through ``Number``; ``padStart(2, "0")`` stays a cosmetic floor, so codes
  beyond a byte (0x104) legitimately render wider.
* ``KEYCAP_SHORT_LABELS`` / ``keycapShortLabel(code)`` — G18 F2 compact
  keycap faces, keyed by HID code, used ONLY for grid keycap text.
* ``keycapFaceLabel(code, physical, capName)`` — G18-FIX1 D1: the TOP keycap
  line went through the same compaction (short label, else physical, else
  catalog name, else ""), plus the 0xa0/0x2f/0x30 map entries the G18 F2 map
  lacked.

The section is extracted verbatim and run under node — no Qt, no browser,
no device I/O. The node binary is found via $OGC_NODE, then ``node`` on
PATH, then the harness-pinned path. When none exists the tests skip.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = (
    Path(__file__).resolve().parents[1]
    / "src" / "open_glorious_core" / "static" / "app.js"
)
START = "// [PURE-HEX-START]"
END = "// [PURE-HEX-END]"
PINNED_NODE = "/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node"


def _node() -> str:
    for candidate in (os.environ.get("OGC_NODE"), shutil.which("node"), PINNED_NODE):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    pytest.skip("no node binary available (set $OGC_NODE)")


def _pure_section() -> str:
    text = APP_JS.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END)
    assert start < end, "pure-hex markers out of order in app.js"
    section = text[start:end]
    # The extracted section must stay importable without a DOM.
    assert "document" not in section
    assert "window" not in section
    return section


def _eval(expr: str):
    script = _pure_section() + f"\nconsole.log(JSON.stringify({expr}));\n"
    result = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        # G18 F1 pinned vectors: NUMBER input, correct radix conversion.
        (10, "0x0A"),
        (29, "0x1D"),
        (0x104, "0x104"),  # wider than a byte: padStart must not truncate
        (0, "0x00"),
        (0xFF, "0xFF"),
        (0xE0, "0xE0"),
    ],
)
def test_keycode_hex_numbers(code: int, expected: str) -> None:
    assert _eval(f"keycodeHex({code})") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # The exact regression: numeric STRINGS (dataset round trips) must be
        # converted, not echoed — String(code).toString(16) was the bug.
        ("10", "0x0A"),
        ("29", "0x1D"),
        ("260", "0x104"),
        ("0", "0x00"),
    ],
)
def test_keycode_hex_numeric_strings(value: str, expected: str) -> None:
    assert _eval(f"keycodeHex({json.dumps(value)})") == expected


def test_keycode_hex_degenerate_input_never_throws() -> None:
    assert _eval("keycodeHex(null)") == "0x00"
    assert _eval("keycodeHex(undefined)") == "0x00"
    assert _eval("keycodeHex('not a number')") == "0x00"
    assert _eval("keycodeHex(-5)") == "0x00"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        # G18 F2 pinned vectors (plan): modifiers, backspace, page up.
        (0xE0, "LCtrl"),
        (0x2A, "Bksp"),
        (0x4B, "PgUp"),
        # Full map (plan list).
        (0xE1, "LShft"),
        (0xE2, "LAlt"),
        (0xE3, "LGui"),
        (0xE4, "RCtrl"),
        (0xE5, "RShft"),
        (0xE6, "RAlt"),
        (0xE7, "RGui"),
        (0x39, "Caps"),
        (0x4E, "PgDn"),
        (0x46, "PrSc"),
        (0x47, "ScrL"),
        (0x48, "Pause"),
        (0x49, "Ins"),
        (0x4C, "Del"),
        (0x53, "NumLk"),
        # G18-FIX1 D1 additions: vendor Fn + brackets (top-line truncation).
        (0xA0, "Fn"),
        (0x2F, "["),
        (0x30, "]"),
        (0x52, "↑"),
        (0x51, "↓"),
        (0x50, "←"),
        (0x4F, "→"),
    ],
)
def test_keycap_short_label_map(code: int, expected: str) -> None:
    assert _eval(f"keycapShortLabel({code})") == expected


def test_keycap_short_label_fallback_is_null() -> None:
    # Codes outside the map (here: "a" 0x04) fall back to the catalog name —
    # the caller (grid render) uses `keycapShortLabel(code) || keymapCodeShort(code)`.
    assert _eval("keycapShortLabel(0x04)") is None
    assert _eval("keycapShortLabel(0xFF)") is None


def test_short_labels_only_rename_no_catalog_key() -> None:
    # Sanity on the pinned map itself: every key is a number, every value a
    # short non-empty string <= 5 chars (keycap faces stay compact).
    pairs = _eval(
        "Object.entries(KEYCAP_SHORT_LABELS).map("
        "([k, v]) => [k, v.length <= 5 && v.length > 0])"
    )
    assert pairs, "KEYCAP_SHORT_LABELS is empty"
    assert all(isinstance(int(k), int) for k, _ok in pairs)
    assert all(ok for _k, ok in pairs)


@pytest.mark.parametrize(
    ("code", "physical", "cap_name", "expected"),
    [
        # G18-FIX1 D1 pinned vectors (plan): the TOP keycap line shows the
        # compact label when mapped, so the modifier row is distinguishable
        # ("LCtrl" vs "LGui" vs "LAlt" — not three identical "Left …").
        (0xE0, "Left Ctrl", "Left Ctrl", "LCtrl"),
        (0xE3, "Left Win", "Left Win", "LGui"),
        (0xA0, "Fn", "Fn (vendor)", "Fn"),
        (0x2F, "Left Bracket", "Left Bracket", "["),
        (0x30, "Right Bracket", "Right Bracket", "]"),
        # No short entry -> the physical name wins.
        (0x04, "A", "A", "A"),
        # Nothing anywhere -> empty face, never "undefined".
        (0xFF, "", "", ""),
    ],
)
def test_keycap_face_label(code: int, physical: str, cap_name: str, expected: str) -> None:
    expr = f"keycapFaceLabel({code}, {json.dumps(physical)}, {json.dumps(cap_name)})"
    assert _eval(expr) == expected


def test_keycap_face_label_fallback_chain() -> None:
    # physical missing -> catalog name wins; both missing -> "".
    assert _eval('keycapFaceLabel(0x04, null, "A")') == "A"
    assert _eval('keycapFaceLabel(0x04, "", "")') == ""


def test_keycap_short_labels_cover_long_catalog_names() -> None:
    # G18-FIX1 D1 (plan): every code in this set carries a catalog name over
    # 8 characters, so each MUST map to a compact short label (<= 5 chars).
    long_name_codes = [
        0x39, 0x2A, 0x4B, 0x4E, 0x46, 0x47, 0x48,
        0x49, 0x4C, 0x53, 0xA0, 0x2F, 0x30,
    ]
    for code in long_name_codes:
        label = _eval(f"KEYCAP_SHORT_LABELS[{code}]")
        assert isinstance(label, str), f"0x{code:02X} has no short label"
        assert 0 < len(label) <= 5, f"0x{code:02X} -> {label!r}"
