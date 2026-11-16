"""G19B commit 2: headless tests for the Macro Editor UI logic.

Two halves:

1. ``[PURE-MACRO-START]``/``[PURE-MACRO-END]`` extraction (the
   tests/test_keymap_hex_js.py pattern): the DOM-free macro helpers from
   ``src/open_glorious_core/static/app.js`` are extracted verbatim and run
   under node (``$OGC_NODE`` -> ``node`` on PATH -> the harness-pinned
   binary; skip when none exists). The drawer-search integration tests
   concatenate ``[PURE-SEARCH...]`` with the macro section — that is exactly
   how app.js composes them at runtime (macroDrawerEntries() entries are
   filtered by keyEntryMatches() inside renderKeymapCodeList).

2. No-node pins on the static files: the EXACT plan footer wording, the
   element ids the app binds, the repeat-mode radio trio ("No Repeat" /
   "Repeat While Holding" / "Toggle"), the drawer "Macros" category, the
   G12 keyevent-logger suppression while recording, and the solid-background
   rules (G14/G18 drawer opacity lessons).

Plan pins honoured here: MacroID 0..9 render M1..M10; 82-step cap
(248 bytes // 3); delay is an integer 1..65535 and 0 is REJECTED upstream —
clampStepDelay only ever serves measured values (recorded gaps) and the
save-payload last-mile guard; e.repeat is dropped; modifiers are ignored
while recording (browser intercepts chords — the UI says so); the store is
the truth — no string here or in the UI claims macros are read from the
keyboard.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = (
    Path(__file__).resolve().parents[1] / "src" / "open_glorious_core" / "static"
)
APP_JS = STATIC / "app.js"
INDEX_HTML = STATIC / "index.html"
STYLES_CSS = STATIC / "styles.css"

MACRO_START = "// [PURE-MACRO-START]"
MACRO_END = "// [PURE-MACRO-END]"
SEARCH_START = "// [PURE-SEARCH-START]"
SEARCH_END = "// [PURE-SEARCH-END]"
PINNED_NODE = "/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node"

FOOTER_NOTE = (
    "Macros live in your profile; click Apply (top-right) to write them to "
    "the board."
)


def _node() -> str:
    for candidate in (os.environ.get("OGC_NODE"), shutil.which("node"), PINNED_NODE):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    pytest.skip("no node binary available (set $OGC_NODE)")


def _section(start: str, end: str) -> str:
    text = APP_JS.read_text(encoding="utf-8")
    lo = text.index(start)
    hi = text.index(end)
    assert lo < hi, f"pure markers out of order: {start}"
    section = text[lo:hi]
    # The extracted sections must stay runnable without a DOM (this also
    # keeps them free of accidental document/window references).
    assert "document" not in section
    assert "window" not in section
    return section


def _macro_section() -> str:
    return _section(MACRO_START, MACRO_END)


def _eval(expr: str):
    return _eval_in(_macro_section(), expr)


def _eval_in(section: str, expr: str):
    script = section + f"\nconsole.log(JSON.stringify({expr}));\n"
    result = subprocess.run(
        [_node(), "-e", script], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# macroLabel: MacroID 0..9 -> M1..M10 (plan: "MacroIDs 0..9 shown M1..M10").
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "M1"),
        ("2", "M3"),
        ("3", "M4"),  # plan E2E "M3" pins BYTES id 3; UI label is M4
        ("9", "M10"),
        ('"2"', "M3"),  # digit strings (store keys) coerce
        ('"-1"', ""),
        ("10", ""),
        ("99", ""),
        ('"x"', ""),
        ("undefined", ""),
    ],
)
def test_macro_label(value: str, expected: str) -> None:
    assert _eval(f"macroLabel({value})") == expected


# ---------------------------------------------------------------------------
# Content / naming / use-count helpers (drawer dots, list column).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("null", False),
        ("{}", False),
        ('{"events": []}', False),
        ('{"events": [{"down": true, "code": 4, "delay": 50}]}', True),
        ('{"events": "nope"}', False),
    ],
)
def test_macro_has_content(entry: str, expected: bool) -> None:
    assert _eval(f"macroHasContent({entry})") is expected


@pytest.mark.parametrize(
    ("macros", "macro_id", "expected"),
    [
        ('{"3": {"name": " Combo "}}', 3, "Combo"),  # trims
        ('{"3": {"name": "   "}}', 3, "M4"),  # blank name -> label
        ('{"3": {}}', 3, "M4"),
        ("{}", 3, "M4"),
        ("null", 7, "M8"),
    ],
)
def test_macro_name_or_label(macros: str, macro_id: int, expected: str) -> None:
    assert _eval(f"macroNameOrLabel({macros}, {macro_id})") == expected


@pytest.mark.parametrize(
    ("slots", "macro_id", "expected"),
    [
        ('{"20": 3, "5": 3, "6": 9}', 3, 2),
        ('{"20": 3, "5": 3, "6": 9}', 9, 1),
        ('{"20": 3, "5": 3, "6": 9}', 0, 0),
        ("{}", 0, 0),
        ("null", 0, 0),
        ('{"7": "2"}', 2, 1),  # string values coerce (store JSON)
    ],
)
def test_macro_use_count(slots: str, macro_id: int, expected: int) -> None:
    assert _eval(f"macroUseCount({slots}, {macro_id})") == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "unused"),
        (1, "used on 1 key"),  # grammar: never "1 keys"
        (2, "used on 2 keys"),
        (120, "used on 120 keys"),
    ],
)
def test_macro_use_label(count: int, expected: str) -> None:
    assert _eval(f"macroUseLabel({count})") == expected


# ---------------------------------------------------------------------------
# Delay policy. clampStepDelay serves MEASURED values (recorded gaps) and
# the save-payload wire guard. Operator-entered 0 is REJECTED at the store/
# API (commit 1, pinned in tests/test_macros.py) — the UI rejects too
# (saveMacro guard); this floor here is not a silent rewrite path.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 1),
        ("-5", 1),
        ("1", 1),
        ("65535", 65535),
        ("65536", 65535),  # vendor upper clamp (plan)
        ("12.7", 13),  # measured floats round
        ("12.4", 12),
        ('"300"', 300),
        ('"abc"', 1),
        ("null", 1),
        ("undefined", 1),
    ],
)
def test_clamp_step_delay(value: str, expected: int) -> None:
    assert _eval(f"clampStepDelay({value})") == expected


@pytest.mark.parametrize(
    ("prev", "now", "expected"),
    [
        ("1000", "1123.7", 124),  # measured gap -> rounded ms
        ("1000", "700", 1),  # timestamp weirdness -> floor, never 0
        ("0", "70000", 65535),  # absurd gap -> vendor clamp
    ],
)
def test_record_step_delta(prev: str, now: str, expected: int) -> None:
    assert _eval(f"recordStepDelta({prev}, {now})") == expected


# ---------------------------------------------------------------------------
# Recorder key filter (plan: ignore e.repeat; ignore modifier keys pressed
# WHILE recording).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("evt", "expected"),
    [
        ("null", True),
        ("undefined", True),
        ('{"repeat": true, "code": "KeyA", "key": "a"}', True),
        ('{"code": "ShiftLeft", "key": "Shift"}', True),
        ('{"code": "ControlRight", "key": "Control"}', True),
        ('{"code": "AltLeft", "key": "Alt"}', True),
        ('{"code": "MetaLeft", "key": "Meta"}', True),
        ('{"code": "KeyA", "key": "Shift"}', True),  # key-set guard too
        ('{"code": "KeyA", "key": "a"}', False),
        ('{"code": "F5", "key": "F5"}', False),
        ('{"code": "Tab", "key": "Tab"}', False),
        ('{"code": "Space", "key": " "}', False),
    ],
)
def test_is_recording_ignored_key(evt: str, expected: bool) -> None:
    assert _eval(f"isRecordingIgnoredKey({evt})") is expected


# ---------------------------------------------------------------------------
# Browser code -> HID table (transcribed from the server's G12-verified
# KEY_EVENT_CODE_VARIANTS; modifiers deliberately absent so recorded
# modifier chords can never become steps).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ('"KeyA"', 4),
        ('"KeyZ"', 29),
        ('"Digit1"', 30),
        ('"Digit0"', 39),
        ('"Enter"', 40),
        ('"Escape"', 41),
        ('"Backspace"', 42),
        ('"Tab"', 43),
        ('"Space"', 44),
        ('"CapsLock"', 57),
        ('"F1"', 58),
        ('"F12"', 69),
        ('"F24"', 115),
        ('"PrintScreen"', 70),
        ('"Delete"', 76),
        ('"ArrowRight"', 79),  # W3C spelling
        ('"RightArrow"', 79),  # harness-table spelling
        ('"ArrowLeft"', 80),
        ('"ArrowDown"', 81),
        ('"ArrowUp"', 82),
        ('"NumLock"', 83),
        ('"NumpadEnter"', 88),
        ('"Numpad0"', 98),
        ('"ContextMenu"', 101),
        ('"VolumeMute"', 127),
        ('"VolumeUp"', 128),
        ('"VolumeDown"', 129),
    ],
)
def test_browser_code_to_hid(code: str, expected: int) -> None:
    assert _eval(f"browserCodeToHid({code})") == expected


@pytest.mark.parametrize(
    "code",
    [
        '"ShiftLeft"',
        '"ControlLeft"',
        '"AltRight"',
        '"MetaLeft"',  # modifiers: absent by design (ignored while recording)
        '"NotAKey"',
        '""',
        "null",
        "5",  # non-string
    ],
)
def test_browser_code_to_hid_null(code: str) -> None:
    assert _eval(f"browserCodeToHid({code})") is None


# ---------------------------------------------------------------------------
# buildMacroSavePayload: the exact /api/macro/save wire shape.
# ---------------------------------------------------------------------------

def test_save_payload_shape_exact_keys_and_coercion() -> None:
    steps = [
        {"down": True, "code": 4, "delay": 120},
        {"down": 0, "code": "4", "delay": 5.2},  # truthiness + coercion
    ]
    expr = (
        "buildMacroSavePayload('3', 'combo', '1', "
        + json.dumps(steps)
        + ")"
    )
    payload = _eval(expr)
    assert payload["id"] == 3  # digit string coerced
    assert payload["name"] == "combo"
    assert payload["repeat"] == 1  # digit string coerced
    assert payload["events"] == [
        {"down": True, "code": 4, "delay": 120},
        {"down": False, "code": 4, "delay": 5},
    ]
    for event in payload["events"]:
        # The store validator demands EXACTLY these keys (commit 1 contract).
        assert set(event) == {"down", "code", "delay"}


def test_save_payload_wire_guard_bounds_and_cap() -> None:
    steps = [{"down": i % 2 == 0, "code": 9, "delay": 0} for i in range(81)]
    # 83 steps in: index 81 carries the absurd 70000 (must clamp to 65535
    # INSIDE the kept window); the 83rd must be dropped by the 82-cap.
    steps += [
        {"down": True, "code": 9, "delay": 70000},
        {"down": False, "code": 9, "delay": 5},
    ]
    payload = _eval(
        "buildMacroSavePayload(0, null, 2, " + json.dumps(steps) + ")"
    )
    assert len(payload["events"]) == 82  # 83 steps capped at the wire limit
    assert payload["name"] == ""  # null name -> empty string (store trims)
    assert payload["repeat"] == 2
    assert payload["events"][0]["delay"] == 1  # measured/absurd floors clamp
    assert payload["events"][81]["delay"] == 65535  # vendor upper clamp


def test_save_payload_degenerate_inputs() -> None:
    payload = _eval("buildMacroSavePayload(undefined, undefined, undefined, 'nope')")
    assert payload == {"id": 0, "name": "", "repeat": 0, "events": []}


# ---------------------------------------------------------------------------
# Drawer "Macros" category — entries AND their real search integration
# (keyEntryMatches from PURE-SEARCH, exactly as renderKeymapCodeList uses
# them).
# ---------------------------------------------------------------------------

MACROS_FIXTURE = json.dumps(
    {
        "0": {"name": "Combo", "repeat": 0, "events": [{"down": True, "code": 4, "delay": 50}]},
        "4": {"name": "Burst", "repeat": 2, "events": []},  # stored but EMPTY
    }
)


def test_drawer_entries_always_ten_with_dots() -> None:
    entries = _eval(f"macroDrawerEntries({MACROS_FIXTURE})")
    assert len(entries) == 10
    zero, four, nine = entries[0], entries[4], entries[9]
    assert zero["filled"] is True
    assert zero["name"] == "M1 · Combo"
    assert four["filled"] is False  # stored header but no events -> hollow
    assert four["name"] == "M5 · Burst"
    assert nine["filled"] is False
    assert nine["name"] == "M10"
    for entry in entries:
        assert entry["macro"] is True
        assert entry["group"] == "macro"
        assert entry["code"] is None  # keeps hex queries off macro entries


def test_drawer_entries_search_integration() -> None:
    section = _section(SEARCH_START, SEARCH_END) + "\n" + _macro_section()

    def matched(query: str) -> list:
        expr = (
            f"macroDrawerEntries({MACROS_FIXTURE}).filter(e => "
            f"keyEntryMatches(e, {json.dumps(query)})).map(e => e.name)"
        )
        return _eval_in(section, expr)

    assert matched("") == [
        "M1 · Combo",
        "M2",
        "M3",
        "M4",
        "M5 · Burst",
        "M6",
        "M7",
        "M8",
        "M9",
        "M10",
    ]
    assert matched("m3") == ["M3"]  # users search the UI LABEL (MacroID 2)
    assert matched("m4") == ["M4"]  # the plan's E2E macro (bytes id 3)
    assert matched("combo") == ["M1 · Combo"]  # alias carries the name
    assert matched("macro") == [
        f"M{i}" if i not in (1, 5) else (f"M{i} · Combo" if i == 1 else f"M{i} · Burst")
        for i in range(1, 11)
    ]
    assert matched("burst") == ["M5 · Burst"]
    assert matched("0x04") == []  # hex queries must NOT hit macro entries
    assert matched("zzz") == []


# ---------------------------------------------------------------------------
# Static-file pins (no node): plan-mandated wording and wiring.
# ---------------------------------------------------------------------------

def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return APP_JS.read_text(encoding="utf-8")


def test_footer_note_is_verbatim_plan_text() -> None:
    html = _html()
    match = re.search(
        r'<p class="macro-footer-note">(.*?)</p>', html, flags=re.DOTALL
    )
    assert match, "macro footer note missing from index.html"
    # HTML collapses whitespace runs to one space when rendering.
    rendered = " ".join(match.group(1).split())
    assert rendered == FOOTER_NOTE


@pytest.mark.parametrize(
    "element_id",
    [
        "macro-section",
        "macro-list",
        "macro-state-chip",
        "macro-apply",
        "macro-editor",
        "macro-editor-title",
        "macro-repeat-group",
        "macro-steps",
        "macro-record",
        "macro-step-key",
        "macro-step-down",
        "macro-step-delay",
        "macro-step-add",
        "macro-save",
        "keymap-editor-macro",
    ],
)
def test_editor_element_ids_exist(element_id: str) -> None:
    assert f'id="{element_id}"' in _html()


def test_repeat_mode_radio_trio() -> None:
    html = _html()
    for label_text in ("No Repeat", "Repeat While Holding", "Toggle"):
        assert f"<strong>{label_text}</strong>" in html, label_text
    group = html[html.index('id="macro-repeat-group"'):]
    group = group[: group.index("</div>")]  # labels hold no nested divs
    assert group.count('type="radio"') == 3
    for value in ('value="0"', 'value="1"', 'value="2"'):
        assert value in group


def test_drawer_macro_category_label() -> None:
    js = _js()
    assert 'macro: "Macros",' in js  # KEYMAP_GROUP_LABELS heading
    order = js[js.index("const KEYMAP_GROUP_ORDER"):]
    order = order[: order.index("];")]
    assert '"macro"' in order  # renders LAST


def test_cap_badge_class_and_drawer_dot() -> None:
    js = _js()
    assert '"keycap-macro-badge"' in js  # "M3"-style top label on caps
    assert "keymap-macro-item" in js
    assert "is-filled" in js


def test_keyevent_logger_is_suppressed_while_recording() -> None:
    js = _js()
    # Anchor on the G12 comment (the file has other keydown listeners).
    anchor = js.index("// G12 item 2: keydown logger")
    listener = js[anchor:]
    listener = listener[listener.index("document.addEventListener"):]
    listener = listener[: listener.index("});")]
    assert "macro.recording" in listener
    assert "postKeyEvent(event.code, event.key);" in listener
    # The suppression must come BEFORE the post.
    assert listener.index("macro.recording") < listener.index("postKeyEvent(")


def test_recorder_listeners_are_window_level() -> None:
    js = _js()
    assert 'window.addEventListener("keydown", (event) => macroRecordEvent(event, true));' in js
    assert 'window.addEventListener("keyup", (event) => macroRecordEvent(event, false));' in js


def test_no_device_readback_wording_for_macros() -> None:
    # Store-is-truth (plan): the UI must never claim macros come from the
    # keyboard. Cheap but effective: ban the read phrasing in the two UI
    # files across all macro contexts.
    for text in (_html(), _js()):
        lowered = text.lower()
        for banned in (
            "macros from the keyboard",
            "read macros",
            "macros read back",
            "readback from the keyboard",
        ):
            assert banned not in lowered, banned


def test_styles_use_solid_backgrounds_for_macro_controls() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    assert "background: #111111;" in css  # name/step/delay/select fields solid
    assert "#macro-record.is-recording" in css
    assert "background: #c92f22;" in css  # recording state is solid red
    assert ".keycap-macro-badge" in css
    assert "pointer-events: none;" in css  # badge never steals cap clicks
