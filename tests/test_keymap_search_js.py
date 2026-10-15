"""Headless tests for the PURE search helpers embedded in static/app.js.

G9 FIX C factors the remap-dropdown matching logic into a DOM-free section
between the ``[PURE-SEARCH-START]`` / ``[PURE-SEARCH-END]`` markers of
``src/open_glorious_core/static/app.js``. This module extracts exactly that
section and runs it under node — no Qt, no browser, no device I/O.

The node binary is found via $OGC_NODE, then ``node`` on PATH, then the
harness-pinned path (plan constraint). When none exists the tests skip.
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
START = "// [PURE-SEARCH-START]"
END = "// [PURE-SEARCH-END]"
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
    assert start < end, "pure-search markers out of order in app.js"
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


KEYPAD_1 = {"code": 0x59, "name": "Keypad 1 and End (0x59)",
            "aliases": ["num1", "numpad1", "kp1", "p1"]}
DIGIT_1 = {"code": 0x1E, "name": "1 (0x1E)", "aliases": []}
F1 = {"code": 0x3A, "name": "F1 (0x3A)", "aliases": []}
F10 = {"code": 0x44, "name": "F10 (0x44)", "aliases": []}
LEFT_CTRL = {"code": 0xE0, "name": "Left Ctrl (0xE0)",
             "aliases": ["ctrl", "lctrl", "leftctrl", "control"]}
VOLUME_UP = {"code": 0x80, "name": "Volume Up (0x80)",
             "aliases": ["volup", "volumeup", "louder"]}
UNASSIGNED = {"code": 0xFF, "name": "Unassigned (0xFF)",
              "aliases": ["none", "blank", "clear", "erase"]}


def _matches(entry: dict, query: str) -> bool:
    return _eval(f"keyEntryMatches({json.dumps(entry)}, {json.dumps(query)})") is True


@pytest.mark.parametrize(
    ("entry", "query", "expected"),
    [
        # alias search (normalized: lowercase, non-alphanumerics stripped)
        (KEYPAD_1, "num1", True),
        (KEYPAD_1, "KP-1", True),
        (KEYPAD_1, "  pad1 junk", False),  # aliases are startswith, not substring
        (KEYPAD_1, "p1", True),
        (LEFT_CTRL, "ctrl", True),
        (LEFT_CTRL, "l-ctrl", True),
        (VOLUME_UP, "vol up", True),
        (F1, "f1", True),
        (F1, "f10", False),
        (F10, "f10", True),
        # hex search: "0x59" and bare "59" find Keypad 1 (0x59)
        (KEYPAD_1, "0x59", True),
        (KEYPAD_1, "0X59", True),
        (KEYPAD_1, " 0x59 ", True),
        (KEYPAD_1, "59", True),
        (KEYPAD_1, "0x5A", False),
        # bare digits are NOT hex: "1" prefix-matches the digits row name
        (DIGIT_1, "1", True),
        (KEYPAD_1, "1", False),
        # name prefix search
        (KEYPAD_1, "keypad 1", True),
        (UNASSIGNED, "unassign", True),
        (UNASSIGNED, "none", True),
        # no match
        (KEYPAD_1, "zzz", False),
    ],
)
def test_key_entry_matches(entry: dict, query: str, expected: bool) -> None:
    assert _matches(entry, query) is expected


def test_empty_query_matches_everything_list_never_empty_on_open() -> None:
    catalog = [KEYPAD_1, DIGIT_1, F1, LEFT_CTRL, VOLUME_UP, UNASSIGNED]
    counts = _eval(
        "(() => { const catalog = "
        + json.dumps(catalog)
        + "; return [catalog.filter((e) => keyEntryMatches(e, '')).length,"
        " catalog.filter((e) => keyEntryMatches(e, '   ')).length]; })()"
    )
    assert counts == [len(catalog), len(catalog)]


def test_normalize_key_token_strips_and_lowercases() -> None:
    assert _eval("normalizeKeyToken('Num-1! End (0x59)')") == "num1end0x59"
    assert _eval("normalizeKeyToken('')") == ""
    assert _eval("normalizeKeyToken(null)") == ""


def test_pure_section_is_self_contained() -> None:
    """The section between the markers runs standalone (no DOM, no helpers)."""
    assert _eval("keyQueryHex('0x59')") == 0x59
    assert _eval("keyQueryHex('59')") == 0x59
    assert _eval("keyQueryHex('ff')") == 0xFF
    assert _eval("keyQueryHex('f1')") == 0xF1
    assert _eval("keyQueryHex('12')") == 0x12
    assert _eval("keyQueryHex('5')") is None  # single digit: text query
    assert _eval("keyQueryHex('0x5')") == 0x05
    assert _eval("keyQueryHex('f1x')") is None
    # The " (0xNN)" suffix is not part of the searchable name.
    assert _eval(f"keyEntrySearchName({json.dumps(F1)})") == "f1"
    assert _eval(f"keyEntrySearchName({json.dumps(KEYPAD_1)})") == "keypad1andend"
