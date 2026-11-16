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

# G19B-FIX1 F1: the footer names the REAL control — the canonical
# MACRO_WRITE_BUTTON_LABEL template ("Apply (top-right)" named nothing that
# exists on this UI).
WRITE_BUTTON_LABEL = "Write macros to keyboard"
FOOTER_NOTE = (
    f"Macros live in your profile; click {WRITE_BUTTON_LABEL} to write them to "
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


# ---------------------------------------------------------------------------
# G19B-FIX1 — copy vs control (F1), snapshot semantics (F2), step-row
# legibility (F3), selection marker (F4), pill interactivity (F5), list
# padding (F6), per-key dead band (F7), name-field state (F8).
# ---------------------------------------------------------------------------

# F1: one canonical control name + two derived templates, no third variant.
def test_write_button_label_constant_is_the_control_name() -> None:
    assert _eval("MACRO_WRITE_BUTTON_LABEL") == WRITE_BUTTON_LABEL
    assert _eval("MACRO_FOOTER_NOTE") == FOOTER_NOTE
    assert _eval("MACRO_PENDING_NOTE") == f"macros waiting for {WRITE_BUTTON_LABEL}"


def test_footer_and_chip_templates_name_the_real_control() -> None:
    section = _macro_section()
    # Both templates are DERIVED from the constant (single source), and the
    # static footer names the control too.
    assert "MACRO_WRITE_BUTTON_LABEL" in section
    assert "`Macros live in your profile; click ${MACRO_WRITE_BUTTON_LABEL}" in section
    assert "`macros waiting for ${MACRO_WRITE_BUTTON_LABEL}`" in section
    html = _html()
    assert "Write macros to keyboard" in html  # macro-apply button label
    footer = re.search(r'<p class="macro-footer-note">(.*?)</p>', html, re.DOTALL)
    # HTML collapses whitespace runs; compare against the collapsed text.
    assert footer and " ".join(footer.group(1).split()).find(WRITE_BUTTON_LABEL) >= 0


def test_no_stale_apply_copy_remains() -> None:
    # The blocker: copy pointed at an "Apply" control that does not exist.
    for text in (_js(), _html()):
        assert "Apply (top-right)" not in text
        assert "waiting for Apply" not in text
    # No third variant: app.js spells the control name as a literal exactly
    # once — in the constant itself; everything else references it.
    assert _js().count(f'"{WRITE_BUTTON_LABEL}"') == 1


# F2: canonical snapshot stringify — key-order-insensitive equal, content-
# sensitive different (pure, headless).
def test_canonical_stringify_sorts_object_keys() -> None:
    # JSON.stringify / Object.keys keep INSERTION order for string keys;
    # the canonical form must not depend on it.
    assert _eval('canonicalMacroJson({z: 1, a: {y: 2, x: 3}})') == \
        '{"a":{"x":3,"y":2},"z":1}'
    # Arrays keep their order — step order IS data.
    assert _eval("canonicalMacroJson([2, 1])") == "[2,1]"


def test_canonical_snapshot_equal_regardless_of_key_order() -> None:
    store_a = (
        '{"0": {"name": "combo", "repeat": 0, "events": '
        '[{"down": true, "code": 4, "delay": 50}]}}'
    )
    store_b = (
        '{"0": {"events": [{"delay": 50, "code": 4, "down": true}], '
        '"name": "combo", "repeat": 0}}'
    )
    assert _eval(
        f"macroStoreSnapshot({store_a}, {{'20': 0}}) "
        f"=== macroStoreSnapshot({store_b}, {{'20': 0}})"
    ) is True


@pytest.mark.parametrize(
    ("macros_b", "slots_b", "why"),
    [
        ('{"0": {"name": "combo", "repeat": 0, "events": '
         '[{"down": true, "code": 4, "delay": 51}]}}',
         {"20": 0}, "delay changed (this is the F2 repro: delete/recreate "
                    "changes CONTENT, a mere POST does not)"),
        ("{}", {"20": 0}, "macro deleted"),
        ('{"1": {"name": "x", "repeat": 0, "events": '
         '[{"down": true, "code": 5, "delay": 9}]}}',
         {"20": 1}, "macro replaced"),
        ('{"0": {"name": "combo", "repeat": 2, "events": '
         '[{"down": true, "code": 4, "delay": 50}]}}',
         {"20": 0}, "repeat mode changed"),
        ("{}", {}, "store emptied (net-zero delete must NOT read as equal "
                   "to the pre-delete snapshot)"),
    ],
)
def test_canonical_snapshot_sensitive_to_content(macros_b, slots_b, why) -> None:
    macros_a = (
        '{"0": {"name": "combo", "repeat": 0, "events": '
        '[{"down": true, "code": 4, "delay": 50}]}}'
    )
    assert _eval(
        f"macroStoreDiffers({macros_a}, {{'20': 0}}, "
        f"macroStoreSnapshot({macros_b}, {json.dumps(slots_b)}))"
    ) is True, why


def test_snapshot_compare_never_claims_pending_without_snapshot() -> None:
    # Store not loaded yet must never light the chip / reveal the button.
    assert _eval("macroStoreDiffers({}, {}, null)") is False
    assert _eval("macroStoreDiffers({'0': {}}, {}, undefined)") is False
    # Equal store vs its own snapshot -> not pending (delete-the-only-macro
    # re-saved identically scenario, and the steady state after a write).
    assert _eval(
        "macroStoreDiffers({'0': {name: 'a'}}, {'20': 0}, "
        "macroStoreSnapshot({'0': {name: 'a'}}, {'20': 0}))"
    ) is False


# F2 (structure): the pending UI keys off the snapshot compare, not a flag.
def test_pending_logic_references_snapshot_compare() -> None:
    js = _js()
    assert (
        "function macroWritePending() {\n"
        "  return macroStoreDiffers(state.macros, state.macroSlots, macro.snapshot);\n"
        "}" in js
    )
    # The old flag is GONE — nothing can re-light the chip without a diff.
    assert "macro.dirty" not in js
    # Every pending consumer calls the compare.
    for func in ("renderKeymapMeta", "renderMacroPanel"):
        body = re.search(
            rf"function {func}\(\) \{{.*?\n\}}", js, flags=re.DOTALL
        )
        assert body and "macroWritePending()" in body.group(0), func
    guard = js[js.index("async function writeKeymapBuffer()"):]
    guard = guard[: guard.index("\nasync function ")]
    assert "!macroWritePending()" in guard  # the macros-only write path
    # Snapshot captured ONCE at page load (first store arrival)...
    load = js[js.index("function applyPayload("):]
    load = load[: load.index("\nfunction ")]
    assert "if (macro.snapshot === null)" in load
    assert "macro.snapshot = macroStoreSnapshot(" in load
    # ...and refreshed on EVERY successful board write (apply + reset).
    writes = js[js.index("async function writeKeymapBuffer()"):
                js.index("async function switchKeymapProfile(")]
    assert writes.count("macro.snapshot = macroStoreSnapshot(") == 2


# F3: ms suffix, aria-labels, enlarged remove control.
def test_step_delay_ms_suffix_and_aria_labels() -> None:
    assert _eval("MACRO_STEP_DELAY_UNIT") == "ms"
    js = _js()
    steps = js[js.index("function renderMacroSteps()"):]
    steps = steps[: steps.index("\nfunction ")]
    assert "MACRO_STEP_DELAY_UNIT" in steps  # suffix rendered from the constant
    assert 'delay.setAttribute("aria-label", `Step ${i + 1} delay in milliseconds`)' in steps
    assert 'remove.setAttribute("aria-label", `Remove step ${i + 1}`)' in steps
    # The toolbar delay input carries an aria-label too (every delay input).
    delay_tag = re.search(r"<input id=\"macro-step-delay\"[^>]*>", _html(), re.DOTALL)
    assert delay_tag and 'aria-label="' in delay_tag.group(0)
    css = STYLES_CSS.read_text(encoding="utf-8")
    unit = re.search(r"\.macro-step-delay-unit \{([^}]*)\}", css)
    assert unit and "var(--muted)" in unit.group(1)


def test_step_remove_control_is_a_real_target() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.macro-step-remove \{([^}]*)\}", css)
    assert rule, ".macro-step-remove rule missing"
    body = rule.group(1)
    for size in re.findall(r"(?:width|height): (\d+)px;", body):
        assert int(size) >= 24, f"hit target too small: {size}px"
    assert "width: 26px" in body and "height: 26px" in body
    assert "color: var(--text);" in body  # higher-contrast × (not muted)
    hover = re.search(r"\.macro-step-remove:hover:not\(:disabled\) \{([^}]*)\}", css)
    assert hover, "remove hover state missing"
    assert "color:" in hover.group(1) and "background:" in hover.group(1)


# F5: the pill toggles — handler registered, interactive styling, tooltip.
def test_down_up_pill_toggles_step_down_flag() -> None:
    js = _js()
    steps = js[js.index("function renderMacroSteps()"):]
    steps = steps[: steps.index("\nfunction ")]
    handler = steps[steps.index('pill.addEventListener("click"'):]
    handler = handler[: handler.index("});")]
    assert "step.down = !step.down" in handler
    assert "renderMacroSteps()" in handler
    assert 'pill.title = "Toggle this step between key press and key release"' in steps
    css = STYLES_CSS.read_text(encoding="utf-8")
    pill = re.search(r"\.macro-step-pill \{([^}]*)\}", css)
    assert pill and "cursor: pointer;" in pill.group(1)
    hover = re.search(r"\.macro-step-pill:hover:not\(:disabled\) \{([^}]*)\}", css)
    assert hover and "box-shadow:" in hover.group(1)  # hover ring


# F4/F6/F7/F8: the static pins on styles.css.
def test_selected_m_row_dot_fills_like_repeat_cards() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.macro-row\.is-active \.macro-dot \{([^}]*)\}", css)
    assert rule, "selected-row dot rule missing"
    assert "background: var(--accent);" in rule.group(1)
    assert "border-color: var(--accent);" in rule.group(1)
    # Same accent-fill language the Repeat-card checked radio uses.
    assert "accent-color: var(--accent);" in css


def test_macro_list_has_bottom_padding_for_m10() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    rule = re.search(r"\.macro-list \{([^}]*)\}", css)
    assert rule and re.search(r"padding-bottom: (?:[6-9]|1\d)px;", rule.group(1)), \
        "M10 needs >=6px of clearance below the list"


def test_per_key_dead_band_carries_one_panel_padding() -> None:
    # F7 (G19A-FIX1 D7 carry-over): the boundary is the existing 18px
    # .control-stack gap + ONE section's 22px padding — this section opts
    # its own top padding out (no new magic number; a structural pin).
    css = STYLES_CSS.read_text(encoding="utf-8")
    rule = re.search(r"#color-controls-section \{([^}]*)\}", css)
    assert rule and "padding-top: 0;" in rule.group(1)
    # The shared rhythm itself must NOT have been retuned.
    stack = re.search(r"\.control-stack \{([^}]*)\}", css)
    assert stack and "gap: 18px;" in stack.group(1)
    assert "padding: 22px;" in css


def test_saved_name_is_value_empty_row_is_placeholder() -> None:
    css = STYLES_CSS.read_text(encoding="utf-8")
    name = re.search(r"\.macro-row-name \{([^}]*)\}", css)
    assert name and "color: var(--text);" in name.group(1)  # VALUE contrast
    placeholder = re.search(r"\.macro-row-name::placeholder \{([^}]*)\}", css)
    assert placeholder, "placeholder rule missing"
    assert "color: var(--muted);" in placeholder.group(1)
    assert "opacity: 1;" in placeholder.group(1)  # kill engine alpha guessing
    disabled = re.search(r"\.macro-row-name:disabled \{([^}]*)\}", css)
    assert disabled and "color: var(--muted);" in disabled.group(1)

