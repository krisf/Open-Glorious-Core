// Slot table of record — the SINGLE source of truth for which matrix slot
// every visual keycap targets. The table below is transcribed row by row from
// remap/KEYMAP_MAP.md (the 120-slot section): every row's slot numbers AND its
// key names come from the map, never from an arithmetic stride. The G11
// "empirical" claim that Caps Lock sits at slot 58 is RETRACTED (G17): the G13
// cell sweep — F13 (0x68) written to 58 and F14 (0x69) to 60, then a Caps Lock
// press — emitted 00 00 69 twice on the hidraw listener and NOTHING from 58.
// The physical CapsLock cell is slot 60, exactly where the vendor
// Buttoninfo_Default puts 0x39; the factory restore that followed ("yes its
// normal again") confirms it. Slot 58 is a dead matrix spare: it belongs to
// the row-4 block's head cells 55-59 (factory 0xFF, no keycap) and is hidden
// from the grid. A grid formula (`row * stride + column`) can silently grow
// per-row offsets; per-row slot LITERALS cannot — row 4's head cell is 60,
// the vendor AND the sweep-confirmed Caps Lock slot.
//
// Rules the table encodes (KEYMAP_MAP.md layout model + SWEEP-VERDICT note):
//   * 6 physical rows x 20-slot USB blocks; each row's VISUAL table lists the
//     row's keycap columns. Row 4's visual block is Caps Lock at slot 60 plus
//     slots 61-74; slots 55, 56, 57, 58, 59 (the factory-0xFF head-of-block
//     cells, 58 included) belong to NO visual row.
//   * `keys[n] === null` = the slot in `slots[n]` carries NO physical keycap
//     (matrix artifacts 14/72/92 — KEYMAP_MAP.md lines 32-42 — and the
//     factory-0xFF row-6 spacer positions 103/104/106/107/111, lines 175-187).
//   * The clickable-cap slot set is exactly the 82 real keycaps: visual
//     slots minus `non_keycap_slots` (which is app.NON_KEYCAP_SLOTS mirrored,
//     including the hidden 55-59 head-of-block cells).
//
// Pure + DOM-free. Loaded by index.html as a classic script BEFORE app.js;
// the python/node regression tests (tests/test_board_matrix_slots.py,
// tests/test_keymap_slots_js.py) parse the JSON block verbatim.
// [KEYMAP-SLOT-TABLE-START]
var KEYMAP_SLOT_TABLE = {
  "rows": [
    {
      "name": "Row 1 — slots 0-19 (KEYMAP_MAP.md lines 46-69)",
      "slots": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
      "keys": ["Esc", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9",
        "F10", "F11", "F12", "PrtSc", null]
    },
    {
      "name": "Row 2 — slots 20-39 (KEYMAP_MAP.md lines 71-94)",
      "slots": [20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34],
      "keys": ["Grave", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
        "Minus", "Equal", "Backspace", "Delete"]
    },
    {
      "name": "Row 3 — slots 40-59 (KEYMAP_MAP.md lines 96-119)",
      "slots": [40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54],
      "keys": ["Tab", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
        "LBracket", "RBracket", "Backslash", "PageUp"]
    },
    {
      "name": "Row 4 — slots 60-79 (KEYMAP_MAP.md lines 121-144); Caps Lock heads the block at its sweep-confirmed slot 60 (G13/G17); the head cells 55-59 have no keycap and are hidden (KEYMAP_MAP.md SWEEP-VERDICT note)",
      "slots": [60, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74],
      "keys": ["Caps Lock", "A", "S", "D", "F", "G", "H", "J", "K", "L",
        "Semicolon", "Quote", null, "Enter", "PageDown"]
    },
    {
      "name": "Row 5 — slots 80-99 (KEYMAP_MAP.md lines 146-169)",
      "slots": [80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94],
      "keys": ["Left Shift", "Z", "X", "C", "V", "B", "N", "M", "Comma",
        "Period", "Slash", "Right Shift", null, "Up", "End"]
    },
    {
      "name": "Row 6 — slots 100-119 (KEYMAP_MAP.md lines 171-194)",
      "slots": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111,
        112, 113, 114],
      "keys": ["Left Ctrl", "Left Win", "Left Alt", null, null, "Space",
        null, null, "Right Alt", "Fn", "Right Ctrl", null, "Left", "Down",
        "Right"]
    }
  ],
  "non_keycap_slots": [14, 55, 56, 57, 58, 59, 72, 92, 103, 104, 106, 107, 111]
};
// [KEYMAP-SLOT-TABLE-END]

// Pure helpers over the table (DOM-free; pinned by tests/test_keymap_slots_js.py).
KEYMAP_SLOT_TABLE.slotRows = function () {
  return KEYMAP_SLOT_TABLE.rows.map(function (row) { return row.slots.slice(); });
};

// Every slot that appears in a visual row table (90 = 6 rows x 15 columns).
KEYMAP_SLOT_TABLE.allVisualSlots = function () {
  var slots = [];
  KEYMAP_SLOT_TABLE.rows.forEach(function (row) {
    slots = slots.concat(row.slots);
  });
  return slots;
};

KEYMAP_SLOT_TABLE.isNonKeycap = function (slot) {
  return KEYMAP_SLOT_TABLE.non_keycap_slots.indexOf(slot) !== -1;
};

// The EXACT set of slots that may render as clickable keycaps: visual slots
// minus every keycap-less position. Slot 60 (Caps Lock, sweep-confirmed by the
// G13 cell sweep) heads row 4; slots 55-59 (the factory-0xFF head-of-block
// cells, dead spare 58 included) are in NO visual row, so no cap can ever bind
// to them; 14/72/92 and the row-6 spacers render as invisible gaps.
KEYMAP_SLOT_TABLE.clickableSlots = function () {
  return KEYMAP_SLOT_TABLE.allVisualSlots().filter(function (slot) {
    return !KEYMAP_SLOT_TABLE.isNonKeycap(slot);
  });
};

// Physical key name of a slot, or null when the slot carries no keycap.
KEYMAP_SLOT_TABLE.capName = function (slot) {
  var rows = KEYMAP_SLOT_TABLE.rows;
  for (var r = 0; r < rows.length; r += 1) {
    var index = rows[r].slots.indexOf(slot);
    if (index !== -1) return rows[r].keys[index];
  }
  return null;
};

// Resolve the cap a human points at ("Caps Lock") to the slot it writes.
KEYMAP_SLOT_TABLE.slotForCapLabel = function (name) {
  var rows = KEYMAP_SLOT_TABLE.rows;
  for (var r = 0; r < rows.length; r += 1) {
    var index = rows[r].keys.indexOf(name);
    if (index !== -1) return rows[r].slots[index];
  }
  return null;
};

// Node-only export for the headless test harness (browser: global only).
if (typeof module !== "undefined" && module.exports) {
  module.exports = KEYMAP_SLOT_TABLE;
}
