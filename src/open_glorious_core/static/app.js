const state = {
  colors: [],
  effects: {},
  effectColorGroups: {},
  devices: [],
  customColor: { red: 255, green: 255, blue: 255 },
  picker: { hue: 0, saturation: 0, value: 1 },
  activeColorGroup: null,
  // G18 F6: preset name matching the keyboard's current RGB (null = custom
  // color or unknown), so the preset chip row can ring the active color.
  activeColorName: null,
  // G18 commit 2: Performance panel mirror of /api/state (null = the
  // backend does not track this setting).
  pollingRate: null,
  inputLatencyMs: null,
  sideLights: null,
  // G19A: win-lock + per-key colors mirror of /api/state (null = backend
  // predates G19A). The local store is the source of truth — this keyboard
  // cannot report colors or the lock state back, so these values always
  // reflect what THIS app saved, never a device read.
  winLock: null,
  perKey: null, // {enabled, brightness, slots} exactly as the store keeps it
  perKeyEdit: false, // Edit mode switch: keycap clicks target the color panel
  perKeySlot: null, // slot the shared picker is currently targeting
  perKeyBusy: false, // bulk op (Paint all / Remove all) in flight
};

const els = {
  backendName: document.getElementById("backend-name"),
  currentDeviceName: document.getElementById("current-device-name"),
  deviceStageTitle: document.getElementById("device-stage-title"),
  statusMessage: document.getElementById("status-message"),
  statusPill: document.getElementById("status-pill"),
  deviceSummary: document.getElementById("device-summary"),
  deviceList: document.getElementById("device-list"),
  brightness: document.getElementById("brightness"),
  brightnessValue: document.getElementById("brightness-value"),
  speed: document.getElementById("speed"),
  speedValue: document.getElementById("speed-value"),
  colorControlsSection: document.getElementById("color-controls-section"),
  colorPanelTitle: document.getElementById("color-panel-title"),
  colorSupportMessage: document.getElementById("color-support-message"),
  colorGroupTabs: document.getElementById("color-group-tabs"),
  colorGrid: document.getElementById("color-grid-buttons") || document.getElementById("color-grid"),
  colorGridWrap: document.getElementById("color-grid"),
  effectGrid: document.getElementById("effect-grid"),
  refreshButton: document.getElementById("refresh-button"),
  flashButton: document.getElementById("flash-button"),
  customColorLayout: document.getElementById("custom-color-layout"),
  customColorSurface: document.getElementById("custom-color-surface"),
  customColorHandle: document.getElementById("custom-color-handle"),
  customColorHue: document.getElementById("custom-color-hue"),
  customColorHex: document.getElementById("custom-color-hex"),
  customColorRed: document.getElementById("custom-color-red"),
  customColorGreen: document.getElementById("custom-color-green"),
  customColorBlue: document.getElementById("custom-color-blue"),
  // G18 commit 2: Performance panel controls.
  pollingGroup: document.getElementById("polling-rate-group"),
  latencyGroup: document.getElementById("input-latency-group"),
  sideLightsToggle: document.getElementById("side-lights-toggle"),
  // G19A: win-lock toggle + per-key colors subsection (Lighting card) +
  // the slot-target strip inside the custom color card.
  winLockToggle: document.getElementById("win-lock-toggle"),
  winLockStatus: document.getElementById("win-lock-status"),
  perKeyEnable: document.getElementById("per-key-enable"),
  perKeyChip: document.getElementById("per-key-chip"),
  perKeyControls: document.getElementById("per-key-controls"),
  perKeyBrightness: document.getElementById("per-key-brightness"),
  perKeyBrightnessValue: document.getElementById("per-key-brightness-value"),
  perKeyEditToggle: document.getElementById("per-key-edit-toggle"),
  perKeyResetAll: document.getElementById("per-key-reset-all"),
  perKeyClearAll: document.getElementById("per-key-clear-all"),
  perKeyStrip: document.getElementById("per-key-strip"),
  perKeyStripLabel: document.getElementById("per-key-strip-label"),
  perKeyVisOff: document.getElementById("per-key-vis-off"),
  perKeyVisBreath: document.getElementById("per-key-vis-breath"),
  perKeyClearSlot: document.getElementById("per-key-clear-slot"),
};

function setText(el, value) {
  if (el) el.textContent = value;
}

function setHtml(el, value) {
  if (el) el.innerHTML = value;
}

function toggleHidden(el, hidden) {
  if (el) el.classList.toggle("is-hidden", hidden);
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function clampByte(value) {
  return clamp(Number.parseInt(value, 10) || 0, 0, 255);
}

// [PURE-SEARCH-START]
// Pure, DOM-free remap-search logic (G9 FIX C). Extracted verbatim between
// these markers and unit-tested headlessly by tests/test_keymap_search_js.py
// (node). Normalization spec: query and every alias/name are lowercased with
// ALL non-alphanumerics stripped; a match is startswith on a normalized alias
// OR startswith on the normalized name (the " (0xNN)" code suffix is NOT part
// of the searchable name); a hex query — "0x59" (1-2 digits) or two bare hex
// chars like "59"/"a6" — matches the code itself. Single bare digits stay
// TEXT queries, so "1" prefix-matches the digits-row name "1" (G9 plan:
// plain single digits are never aliased). Empty query matches every entry —
// the dropdown opens showing the FULL grouped list, never empty.
function normalizeKeyToken(value) {
  return String(value == null ? "" : value).toLowerCase().replace(/[^a-z0-9]/g, "");
}

function keyQueryHex(value) {
  const token = normalizeKeyToken(value);
  let hex = null;
  const prefixed = /^0x([0-9a-f]{1,2})$/.exec(token);
  if (prefixed) {
    hex = Number.parseInt(prefixed[1], 16);
  } else if (/^[0-9a-f]{2}$/.test(token)) {
    // Exactly two bare hex chars ("59", "a6", "ff"): hex. Single chars and
    // longer strings are text queries.
    hex = Number.parseInt(token, 16);
  }
  return hex;
}

function keyEntrySearchName(entry) {
  // Strip the " (0xNN)" code suffix: searchable name stays "F1", not
  // "f10x3a" (which made query "f10" prefix-match F1).
  return normalizeKeyToken(String(entry.name == null ? "" : entry.name)
    .replace(/ \(0x[0-9A-Fa-f]{2}\)$/, ""));
}

function keyEntryMatches(entry, query) {
  const token = normalizeKeyToken(query);
  if (!token) return true;
  const hex = keyQueryHex(query);
  if (hex !== null && entry.code === hex) return true;
  if (keyEntrySearchName(entry).startsWith(token)) return true;
  const aliases = entry.aliases || [];
  for (const alias of aliases) {
    if (normalizeKeyToken(alias).startsWith(token)) return true;
  }
  return false;
}
// [PURE-SEARCH-END]

// [PURE-DRAWER-START]
// Pure, DOM-free drawer logic (G10 FIX 4, refined G17). Extracted verbatim
// between these markers and unit-tested headlessly by tests/test_keymap_slots_js.py (node).
// A slot whose FACTORY code is 0xFF is an unassigned matrix slot per the
// VENDOR table: the drawer shows the unassigned note INSTEAD of the normal
// editor for it. `slotHasKeycap === true` means the board map puts a physical
// keycap on this slot — it wins over the vendor's 0xFF, so a factory-0xFF cell
// that carries a real cap (the head-of-block spares are NOT caps; Caps Lock
// sits at slot 60 with factory 0x39, G13 sweep/G17) stays editable.
function keycapSlotIsUnassigned(meta, slotHasKeycap) {
  if (!meta) return false;
  if (slotHasKeycap === true) return false;
  return Number(meta.factory_code) === 0xFF;
}
// [PURE-DRAWER-END]

// [PURE-HEX-START]
// Pure, DOM-free label logic (G18 F1/F2). Extracted verbatim between these
// markers and unit-tested headlessly by tests/test_keymap_hex_js.py (node).
// F1: hex labels are built from a NUMBER, never from String(code) —
// `.toString(16)` on a STRING returns the string itself (no radix), so codes
// above 0xFF (e.g. 0x104) rendered as garbage. padStart(2, "0") is only a
// cosmetic floor: 0x104 legitimately stays "0x104".
function keycodeHex(code) {
  const num = Number(code);
  if (!Number.isFinite(num) || num < 0) return "0x00";
  return `0x${Math.trunc(num).toString(16).toUpperCase().padStart(2, "0")}`;
}

// F2: dense keycap faces need compact labels. Keyed by HID code; used ONLY
// for the grid keycap text — tooltips, the remap editor and the search list
// keep the full catalog name. Codes outside the map keep their catalog name.
const KEYCAP_SHORT_LABELS = {
  0xe0: "LCtrl",
  0xe1: "LShft",
  0xe2: "LAlt",
  0xe3: "LGui",
  0xe4: "RCtrl",
  0xe5: "RShft",
  0xe6: "RAlt",
  0xe7: "RGui",
  0x39: "Caps",
  0x2a: "Bksp",
  0x4b: "PgUp",
  0x4e: "PgDn",
  0x46: "PrSc",
  0x47: "ScrL",
  0x48: "Pause",
  0x49: "Ins",
  0x4c: "Del",
  0x53: "NumLk",
  // G18-FIX1 D1: vendor Fn (catalog name "Fn (vendor)") and the brackets —
  // all three catalog names are too long for a cap face (0xa0 truncated on
  // the BOTTOM line too, 0x2f/0x30 truncated the top one).
  0xa0: "Fn",
  0x2f: "[",
  0x30: "]",
  // G18-FIX2: the two remaining truncated faces — 9-char catalog names whose
  // bottom line already shows the glyph, so the top line gets it too.
  0x31: "\\",
  0x33: ";",
  0x52: "↑",
  0x51: "↓",
  0x50: "←",
  0x4f: "→",
};

// Compact keycap label for a code, or null when the catalog name should stay.
function keycapShortLabel(code) {
  const short = KEYCAP_SHORT_LABELS[Number(code)];
  return short === undefined ? null : short;
}

// G18-FIX1 D1: full face label for the TOP keycap line — the compact short
// label wins when the code is mapped, else the physical name, else the
// catalog name. (The G18 F2 fix only compacted the BOTTOM line, so the top
// row rendered "Left …" three times over LCtrl/LGui/LAlt.) The renderer
// keeps the FULL name on the tooltip.
function keycapFaceLabel(code, physical, capName) {
  return keycapShortLabel(code) || physical || capName || "";
}
// [PURE-HEX-END]

// [PURE-PERKEY-START]
// G19A: the ONE canonical map of the three per-key visibility modes. Bytes
// are NUMBERS taken straight from the vendor layout spec (see the map — it
// is deliberately the only place the three byte literals appear). Nothing
// here parses a radix, and perKeyVisLabel FORMATS the number via the shared
// keycodeHex (G18 F1 lesson: formatting a STRING echoes its decimal text
// verbatim — the hex labels must be produced from numbers).
const PER_KEY_VIS_MODES = [
  { name: "off", byte: 0x00, label: "OFF" },
  { name: "lit", byte: 0xFF, label: "Lit" },
  { name: "breath", byte: 0xFE, label: "Breathing" },
];
const PER_KEY_VIS_BYTES = PER_KEY_VIS_MODES.reduce((map, mode) => {
  map[mode.name] = mode.byte;
  return map;
}, {});

function perKeyVisByte(vis) {
  const name = String(vis);
  return Object.prototype.hasOwnProperty.call(PER_KEY_VIS_BYTES, name)
    ? PER_KEY_VIS_BYTES[name]
    : null;
}

// Hex label for the strip (e.g. "breath" renders as its FE byte, uppercase
// hex string — formatted from the map's number, never parsed from a string).
function perKeyVisLabel(vis) {
  const byte = perKeyVisByte(vis);
  return byte == null ? "—" : keycodeHex(byte);
}

function perKeyVisName(vis) {
  const mode = PER_KEY_VIS_MODES.find((entry) => entry.name === String(vis));
  return mode ? mode.label : "—";
}

// Dedicated settings routes (commit 1 wired them; exact pinned strings).
const PER_KEY_ROUTES = {
  winLock: "/api/win-lock",
  enable: "/api/per-key/enable",
  brightness: "/api/per-key/brightness",
  color: "/api/per-key/color",
  clear: "/api/per-key/clear",
};

function perKeyColoredCount(slots) {
  if (!slots || typeof slots !== "object") return 0;
  return Object.keys(slots).length;
}

function perKeyChipText(slots) {
  return `${perKeyColoredCount(slots)} keys colored`;
}

// Keycap bottom-face tint: lit/breathing slots tint, OFF slots (and
// malformed entries) do not. Returns the [r, g, b] triple or null — the
// caller formats it with the shared rgbToHex.
function perKeyTintRgb(perKey, slot) {
  const slots = perKey && perKey.slots;
  if (!perKey || !perKey.enabled || !slots) return null;
  const entry = slots[String(slot)];
  if (!entry || entry.vis === "off") return null;
  const rgb = entry.rgb;
  if (!Array.isArray(rgb) || rgb.length !== 3) return null;
  if (rgb.some((value) => !Number.isInteger(value) || value < 0 || value > 255)) {
    return null;
  }
  return rgb;
}

// Edit-mode gating (pinned semantics): a keycap click opens the color panel
// ONLY while per-key mode is enabled AND Edit mode is on; in every other
// combination the keycap click keeps its current remap-drawer behavior.
// The two destinations are mutually exclusive — never both.
function perKeyEditActive(enabled, editMode) {
  return Boolean(enabled) && Boolean(editMode);
}

function perKeyClickOpensColorPanel(enabled, editMode) {
  return perKeyEditActive(enabled, editMode);
}

function perKeyClickOpensDrawer(enabled, editMode) {
  return !perKeyClickOpensColorPanel(enabled, editMode);
}

// G19A-FIX1 D1: the ONE status-text map for the win-lock row — the
// Performance render maps the saved boolean through this function, and no
// other place in the app spells these two strings (the toggle sits in the
// fixed right-edge column exactly like side-lights' "Light bar on").
function winLockStatusText(winLock) {
  return winLock ? "Windows key locked" : "Windows key unlocked";
}

// G19A-FIX1 D2: ONE canonical slot-label source. The strip label AND the
// picker subtitle both format through perKeySlotTag (numbers stay NUMBERS —
// G18 radix lesson), so the slot number shown in the two places can never
// drift apart, and the subtitle can never contradict the strip.
function perKeySlotTag(slot) {
  return `Slot ${Number(slot)}`;
}

function perKeyStripLabel(slot, entry) {
  const tag = perKeySlotTag(slot);
  if (!entry) {
    return `${tag} · no color saved yet — picking one stores it as Lit (${perKeyVisLabel("lit")})`;
  }
  return `${tag} · ${perKeyVisName(entry.vis)} (${perKeyVisLabel(entry.vis)})`;
}

// Strip active -> the slot subtitle; strip hidden -> the caller's original
// effect subtitle is restored verbatim.
function perKeyPickerSubtitle(stripActive, slot, effectSubtitle) {
  return stripActive ? `Editing the color of ${perKeySlotTag(slot)}` : effectSubtitle;
}
// [PURE-PERKEY-END]

// [PURE-PICKER-START]
// Pure, DOM-free picker math (G19A-FIX1 D5). Extracted verbatim between
// these markers and unit-tested headlessly by tests/test_perkey_js.py
// (node). The SV handle sits at (saturation * width, (1 - value) * height);
// at the extreme corners the container's rounding clipped the circle, so
// positioning clamps the offset into [radius, dimension - radius].
function pickerHandleOffset(raw, dimension, radius) {
  const r = Number(radius);
  const lo = r;
  const hi = Math.max(Number(dimension) - r, r);
  return Math.max(lo, Math.min(hi, Number(raw)));
}
// [PURE-PICKER-END]

function rgbToHex(red, green, blue) {
  return `#${[red, green, blue].map((value) => clampByte(value).toString(16).padStart(2, "0")).join("")}`.toUpperCase();
}

function hexToRgb(hex) {
  const normalized = String(hex).trim().replace(/^#?/, "#");
  if (!/^#[0-9a-fA-F]{6}$/.test(normalized)) return null;
  return {
    red: Number.parseInt(normalized.slice(1, 3), 16),
    green: Number.parseInt(normalized.slice(3, 5), 16),
    blue: Number.parseInt(normalized.slice(5, 7), 16),
  };
}

function rgbToHsv(red, green, blue) {
  const r = clampByte(red) / 255;
  const g = clampByte(green) / 255;
  const b = clampByte(blue) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const delta = max - min;

  let hue = 0;
  if (delta !== 0) {
    if (max === r) hue = 60 * (((g - b) / delta) % 6);
    else if (max === g) hue = 60 * (((b - r) / delta) + 2);
    else hue = 60 * (((r - g) / delta) + 4);
  }
  if (hue < 0) hue += 360;

  return {
    hue,
    saturation: max === 0 ? 0 : delta / max,
    value: max,
  };
}

function hsvToRgb(hue, saturation, value) {
  const h = ((Number(hue) % 360) + 360) % 360;
  const s = clamp(Number(saturation), 0, 1);
  const v = clamp(Number(value), 0, 1);
  const chroma = v * s;
  const x = chroma * (1 - Math.abs(((h / 60) % 2) - 1));
  const match = v - chroma;

  let r = 0;
  let g = 0;
  let b = 0;
  if (h < 60) {
    r = chroma; g = x;
  } else if (h < 120) {
    r = x; g = chroma;
  } else if (h < 180) {
    g = chroma; b = x;
  } else if (h < 240) {
    g = x; b = chroma;
  } else if (h < 300) {
    r = x; b = chroma;
  } else {
    r = chroma; b = x;
  }

  return {
    red: Math.round((r + match) * 255),
    green: Math.round((g + match) * 255),
    blue: Math.round((b + match) * 255),
  };
}

function getColorGroups(effectId) {
  const key = String(effectId ?? "");
  return state.effectColorGroups[key] || [];
}

function getActiveEffectId() {
  const active = document.querySelector(".effect-button.is-active");
  return active ? Number(active.dataset.effectId) : null;
}

function renderPicker() {
  if (els.customColorSurface) {
    els.customColorSurface.style.setProperty("--picker-hue", String(Math.round(state.picker.hue)));
  }
  if (els.customColorHandle) {
    // G19A-FIX1 D5: inset the handle by half its diameter — clamp its
    // left/top into [r, width-r] / [r, height-r] so the full circle stays
    // visible at the container corners. The saturation/value mapping itself
    // is unchanged.
    const rect = els.customColorSurface ? els.customColorSurface.getBoundingClientRect() : null;
    if (rect && rect.width > 0 && rect.height > 0) {
      const radius = (els.customColorHandle.offsetWidth || 18) / 2;
      els.customColorHandle.style.left = `${pickerHandleOffset(state.picker.saturation * rect.width, rect.width, radius)}px`;
      els.customColorHandle.style.top = `${pickerHandleOffset((1 - state.picker.value) * rect.height, rect.height, radius)}px`;
    } else {
      // No measured surface (detached/pre-layout): keep the percentage math.
      els.customColorHandle.style.left = `${state.picker.saturation * 100}%`;
      els.customColorHandle.style.top = `${(1 - state.picker.value) * 100}%`;
    }
  }
  if (els.customColorHue) {
    els.customColorHue.value = String(Math.round(state.picker.hue));
  }
}

function syncCustomColorInputs(red, green, blue) {
  state.customColor = {
    red: clampByte(red),
    green: clampByte(green),
    blue: clampByte(blue),
  };
  state.picker = rgbToHsv(state.customColor.red, state.customColor.green, state.customColor.blue);

  const hex = rgbToHex(state.customColor.red, state.customColor.green, state.customColor.blue);
  if (els.customColorHex) els.customColorHex.value = hex;
  if (els.customColorRed) els.customColorRed.value = String(state.customColor.red);
  if (els.customColorGreen) els.customColorGreen.value = String(state.customColor.green);
  if (els.customColorBlue) els.customColorBlue.value = String(state.customColor.blue);
  renderPicker();
}

let customColorTimer = null;

// G19A: true while per-key Edit mode has a slot targeted — the shared
// picker/RGB/hex machinery then writes that slot, not the effect color.
function perKeyTargetActive() {
  return (
    perKeyEditActive(state.perKey && state.perKey.enabled, state.perKeyEdit)
    && state.perKeySlot != null
  );
}

function commitCustomColor() {
  if (customColorTimer) clearTimeout(customColorTimer);
  customColorTimer = setTimeout(() => {
    if (perKeyTargetActive()) {
      // Slot color: rgb only — the backend keeps the slot's vis mode.
      postPerKey("color", {
        slot: state.perKeySlot,
        rgb: [state.customColor.red, state.customColor.green, state.customColor.blue],
      });
      return;
    }
    sendAction("set_custom_color", {
      ...state.customColor,
      group: state.activeColorGroup,
    });
  }, 120);
}

function updateColorFromSurfaceEvent(event) {
  if (!els.customColorSurface) return;
  const rect = els.customColorSurface.getBoundingClientRect();
  const x = clamp((event.clientX - rect.left) / rect.width, 0, 1);
  const y = clamp((event.clientY - rect.top) / rect.height, 0, 1);
  state.picker.saturation = x;
  state.picker.value = 1 - y;
  const rgb = hsvToRgb(state.picker.hue, state.picker.saturation, state.picker.value);
  syncCustomColorInputs(rgb.red, rgb.green, rgb.blue);
  commitCustomColor();
}

function bindSlider(input, output, action) {
  if (!input || !output) return;
  let timer = null;

  const update = () => {
    output.textContent = input.value;
  };

  const commit = () => {
    if (!action) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => sendAction(action, input.value), 120);
  };

  input.addEventListener("input", () => {
    update();
    commit();
  });
  update();
}

function bindCustomColorControls() {
  if (els.customColorSurface) {
    let dragging = false;
    els.customColorSurface.addEventListener("pointerdown", (event) => {
      dragging = true;
      els.customColorSurface.setPointerCapture(event.pointerId);
      updateColorFromSurfaceEvent(event);
    });
    els.customColorSurface.addEventListener("pointermove", (event) => {
      if (dragging) updateColorFromSurfaceEvent(event);
    });
    const stopDragging = (event) => {
      if (!dragging) return;
      dragging = false;
      if (els.customColorSurface.hasPointerCapture(event.pointerId)) {
        els.customColorSurface.releasePointerCapture(event.pointerId);
      }
    };
    els.customColorSurface.addEventListener("pointerup", stopDragging);
    els.customColorSurface.addEventListener("pointercancel", stopDragging);
  }

  if (els.customColorHue) {
    els.customColorHue.addEventListener("input", () => {
      state.picker.hue = Number(els.customColorHue.value) || 0;
      const rgb = hsvToRgb(state.picker.hue, state.picker.saturation, state.picker.value);
      syncCustomColorInputs(rgb.red, rgb.green, rgb.blue);
      commitCustomColor();
    });
  }

  if (els.customColorHex) {
    els.customColorHex.addEventListener("change", () => {
      const rgb = hexToRgb(els.customColorHex.value);
      if (!rgb) {
        syncCustomColorInputs(state.customColor.red, state.customColor.green, state.customColor.blue);
        return;
      }
      syncCustomColorInputs(rgb.red, rgb.green, rgb.blue);
      commitCustomColor();
    });
  }

  [els.customColorRed, els.customColorGreen, els.customColorBlue].forEach((input) => {
    if (!input) return;
    input.addEventListener("input", () => {
      syncCustomColorInputs(
        els.customColorRed?.value ?? state.customColor.red,
        els.customColorGreen?.value ?? state.customColor.green,
        els.customColorBlue?.value ?? state.customColor.blue,
      );
      commitCustomColor();
    });
  });
}

function renderColorGroupTabs(activeEffect) {
  const groups = getColorGroups(activeEffect);
  if (!els.colorGroupTabs) return groups;

  if (groups.length <= 1) {
    els.colorGroupTabs.innerHTML = "";
    toggleHidden(els.colorGroupTabs, true);
    state.activeColorGroup = groups[0] || null;
    return groups;
  }

  toggleHidden(els.colorGroupTabs, false);
  if (!groups.includes(state.activeColorGroup)) {
    state.activeColorGroup = groups[0];
  }

  els.colorGroupTabs.innerHTML = "";
  for (const group of groups) {
    const button = document.createElement("button");
    button.className = "button button-secondary color-group-tab";
    button.textContent = group;
    button.classList.toggle("is-active", state.activeColorGroup === group);
    button.addEventListener("click", () => {
      state.activeColorGroup = group;
      renderColorGroupTabs(activeEffect);
    });
    els.colorGroupTabs.appendChild(button);
  }
  return groups;
}

// G19A-FIX1 D2: the ORIGINAL (strip-inactive) picker subtitle strings live
// in ONE place.
function effectColorSubtitle(groups) {
  if (!groups.length) {
    return "This effect currently uses the firmware palette and does not expose custom colors in this Linux app yet.";
  }
  return groups.length > 1
    ? "Choose a color group tab, then set the color for that part of the effect."
    : "This effect supports a single custom color.";
}

// G19A-FIX1 D2: the picker subtitle ALWAYS renders through the ONE canonical
// perKeyPickerSubtitle — an active per-key slot strip swaps in
// 'Editing the color of Slot N' (same slot state that drives the strip
// label); an inactive strip restores the effect subtitle verbatim.
function renderColorSubtitle(groups) {
  setText(
    els.colorSupportMessage,
    perKeyPickerSubtitle(perKeyTargetActive(), state.perKeySlot, effectColorSubtitle(groups)),
  );
}

function updateColorControls(activeEffect) {
  const groups = renderColorGroupTabs(activeEffect);
  const supportsColor = groups.length > 0;

  // G19A: while a per-key slot is targeted the picker stays available even
  // for palette-only effects — it colors the SLOT, not the effect.
  toggleHidden(els.customColorLayout, !supportsColor && !perKeyTargetActive());
  toggleHidden(els.colorGridWrap, !supportsColor);

  if (!supportsColor) {
    setText(els.colorPanelTitle, "Not Available For This Effect");
    state.activeColorGroup = null;
  } else {
    setText(els.colorPanelTitle, groups.length > 1 ? "Group Colors" : "Picker, Hex & RGB");
    if (!state.activeColorGroup) {
      state.activeColorGroup = groups[0] || null;
    }
  }
  renderColorSubtitle(groups);
}

document.addEventListener("contextmenu", (event) => {
  event.preventDefault();
});

bindSlider(els.brightness, els.brightnessValue, "set_brightness");
bindSlider(els.speed, els.speedValue, "set_speed");
bindCustomColorControls();

async function fetchState() {
  const response = await fetch("/api/state");
  const payload = await response.json();
  applyPayload(payload);
}

async function postAction(action, value = null) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, value }),
  });
  const payload = await response.json();
  applyPayload(payload);
  return payload;
}

async function sendAction(action, value = null) {
  await postAction(action, value);
}

function applyPayload(payload) {
  state.colors = payload.colors || state.colors;
  state.effects = payload.effects || state.effects;
  state.effectColorGroups = payload.effect_color_groups || state.effectColorGroups;
  state.devices = payload.devices || [];

  const selectedDevice = state.devices[0] || null;
  const selectedName = selectedDevice?.name || "No Device Detected";
  const activeEffect = payload.state?.effect;

  setText(els.backendName, payload.backend || "Unknown Backend");
  setText(els.currentDeviceName, selectedName);
  setText(els.deviceStageTitle, selectedName);
  setText(els.statusMessage, payload.message || "No message");
  setText(els.statusPill, payload.ok ? "Connected" : "Needs Attention");
  if (els.statusPill) {
    els.statusPill.classList.toggle("ok", Boolean(payload.ok));
  }

  if (payload.state) {
    if (payload.state.brightness != null && els.brightness) els.brightness.value = payload.state.brightness;
    if (payload.state.speed != null && els.speed) els.speed.value = payload.state.speed;
    if (els.brightness && els.brightnessValue) setText(els.brightnessValue, els.brightness.value);
    if (els.speed && els.speedValue) setText(els.speedValue, els.speed.value);
    if (payload.state.red != null && payload.state.green != null && payload.state.blue != null) {
      if (perKeyTargetActive()) {
        // G19A: while a per-key slot is targeted the picker shows the slot's
        // SAVED color, never the global effect color.
        syncPerKeyTargetInputs();
      } else {
        syncCustomColorInputs(payload.state.red, payload.state.green, payload.state.blue);
      }
    }
    // G18 F6: recompute the active preset on EVERY state render.
    state.activeColorName = colorNameForRgb(
      payload.state.red, payload.state.green, payload.state.blue,
    );
    // G18 commit 2: Performance panel mirror follows the payload.
    if (payload.state.polling_rate != null) state.pollingRate = payload.state.polling_rate;
    if (payload.state.input_latency_ms != null) state.inputLatencyMs = payload.state.input_latency_ms;
    if (payload.state.side_lights != null) state.sideLights = Boolean(payload.state.side_lights);
    // G19A: win-lock + per-key mirror. These are the values THIS app saved
    // (the store is the source of truth) — this board has no readback.
    if (payload.state.win_lock != null) state.winLock = Boolean(payload.state.win_lock);
    if (payload.state.per_key) state.perKey = payload.state.per_key;
  } else {
    state.activeColorName = null;
  }

  renderDevices(payload.backend);
  renderDeviceMeta(selectedDevice);
  renderColors();
  renderEffects(activeEffect);
  renderPerformance();
  updateColorControls(activeEffect);
  // G19A: win-lock toggle, per-key subsection, keycap tints + strip.
  renderPerKeyPanel();
  renderKeymapGrid();
}

function renderDevices(backendName) {
  if (!els.deviceList) return;
  if (!state.devices.length) {
    els.deviceList.innerHTML = '<div class="device-card"><img src="./assets/gmmkpro-selected.svg" alt="Device icon"><div><strong>No keyboard found</strong><span>Plug in your GMMK Pro to begin.</span></div></div>';
    return;
  }

  els.deviceList.innerHTML = "";
  state.devices.forEach((device, index) => {
    const card = document.createElement("div");
    card.className = "device-card";
    card.classList.toggle("is-active", index === 0);
    // G18 F5: the raw hidraw paths are developer detail — collapsed out of
    // the card's face, one structured line per path (no mid-token wrapping).
    const hidrawItems = (device.hidraws || [])
      .map((path) => `<li>${path}</li>`)
      .join("");
    const details = hidrawItems
      ? `<details class="device-details"><summary>Device details</summary><ul>${hidrawItems}</ul></details>`
      : "";
    card.innerHTML = `
      <img src="./assets/gmmkpro-selected.svg" alt="GMMK Pro icon">
      <div>
        <strong>${device.name}</strong>
        <span class="device-card-subtitle" title="${backendName}">${backendName}</span>
        ${details}
      </div>
    `;
    els.deviceList.appendChild(card);
  });
}

function renderDeviceMeta(device) {
  if (!els.deviceSummary) return;
  const items = [];
  // G18 F5: hidraw paths moved into the card's collapsed "Device details";
  // the summary chips keep the short, human-readable metadata only.
  if (device?.interfaces?.length) items.push(`USB functions: ${device.interfaces.join(", ")}`);
  if (device?.vendor_id && device?.product_id) items.push(`USB: ${device.vendor_id}:${device.product_id}`);
  if (state.colors.length) items.push(`${state.colors.length} color presets`);
  if (Object.keys(state.effects).length) items.push(`${Object.keys(state.effects).length} effects loaded`);
  setHtml(els.deviceSummary, items.map((item) => `<span class="meta-chip">${item}</span>`).join(""));
}

// G18 F6: mirror of the backend wire COLOR_MAP (backend.py) — the /api/state
// payload carries the resolved RGB, and the preset whose map entry equals it
// is the active color. A custom color equals no preset, so the ring clears.
const COLOR_PRESET_RGB = {
  red: [255, 0, 0],
  yellow: [255, 255, 0],
  green: [0, 255, 0],
  cyan: [0, 255, 255],
  blue: [0, 0, 255],
  purple: [128, 0, 255],
  white: [255, 255, 255],
};

function colorNameForRgb(red, green, blue) {
  if (red == null || green == null || blue == null) return null;
  for (const [name, rgb] of Object.entries(COLOR_PRESET_RGB)) {
    if (rgb[0] === red && rgb[1] === green && rgb[2] === blue) return name;
  }
  return null; // custom color (or unknown) — no preset is active
}

function renderColors() {
  if (!els.colorGrid) return;
  if (!state.colors.length) {
    els.colorGrid.innerHTML = "";
    return;
  }

  const previewMap = {
    red: "#ff4f47",
    yellow: "#f5c84c",
    green: "#43b866",
    cyan: "#33c6d6",
    blue: "#3e6df6",
    purple: "#8e56ff",
    white: "#f5f5f0",
  };

  els.colorGrid.innerHTML = "";
  for (const color of state.colors) {
    const button = document.createElement("button");
    button.className = "color-chip";
    // G18 F6: ring the chip matching the keyboard's current color; every
    // ring is cleared while a custom color is active (activeColorName null).
    button.classList.toggle("is-active", state.activeColorName === color);
    button.setAttribute("aria-pressed", state.activeColorName === color ? "true" : "false");
    button.innerHTML = `<span class="color-preview" style="background:${previewMap[color] || "#ddd"}"></span><span>${color}</span>`;
    button.addEventListener("click", () => sendAction("set_color", color));
    els.colorGrid.appendChild(button);
  }
}

// G18 commit 2: the Performance panel renders from the /api/state mirror on
// every state update — segmented buttons ring the active value, the side
// lights toggle follows the stored boolean.
function renderPerformance() {
  const markSegmented = (group, datasetKey, value) => {
    if (!group) return;
    for (const btn of group.querySelectorAll(".segment")) {
      btn.classList.toggle("is-active", btn.dataset[datasetKey] === String(value));
    }
  };
  markSegmented(els.pollingGroup, "rate", state.pollingRate);
  markSegmented(els.latencyGroup, "latency", state.inputLatencyMs);
  if (els.sideLightsToggle && state.sideLights != null) {
    els.sideLightsToggle.checked = state.sideLights;
  }
  // G19A: the win-lock toggle follows the saved flag (store value — there is
  // no device readback for it on this board). G19A-FIX1 D1: the short inline
  // status text renders through the ONE status map (winLockStatusText).
  if (els.winLockToggle && state.winLock != null) {
    els.winLockToggle.checked = state.winLock;
    setText(els.winLockStatus, winLockStatusText(state.winLock));
  }
}

// --- G19A: per-key colors -----------------------------------------------------
// The store holds every color; /api/state mirrors it. This keyboard cannot
// report its colors back, so these renders are the UI's only truth — and the
// wording in index.html never claims a device read.
async function postPerKey(routeKey, value) {
  const path = PER_KEY_ROUTES[routeKey];
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    const payload = await response.json();
    // Bulk ops (Paint every key white / Remove all colors) suppress the
    // per-op re-render; the final fetchState() renders everything once.
    if (!state.perKeyBusy) applyPayload(payload);
    return payload;
  } catch (error) {
    setText(els.statusMessage, `Request to ${path} failed: ${error}`);
    setText(els.statusPill, "Error");
    return { ok: false, message: String(error) };
  }
}

function renderPerKeyPanel() {
  const perKey = state.perKey;
  if (!perKey) return;
  if (els.perKeyEnable) els.perKeyEnable.checked = Boolean(perKey.enabled);
  toggleHidden(els.perKeyControls, !perKey.enabled);
  if (els.perKeyBrightness && document.activeElement !== els.perKeyBrightness) {
    els.perKeyBrightness.value = String(perKey.brightness);
  }
  if (els.perKeyBrightnessValue) {
    setText(els.perKeyBrightnessValue, String(perKey.brightness));
  }
  if (els.perKeyChip) setText(els.perKeyChip, perKeyChipText(perKey.slots));
  if (els.perKeyEditToggle) {
    els.perKeyEditToggle.checked = state.perKeyEdit;
    els.perKeyEditToggle.disabled = !perKey.enabled;
  }
  if (els.perKeyResetAll) els.perKeyResetAll.disabled = state.perKeyBusy;
  if (els.perKeyClearAll) els.perKeyClearAll.disabled = state.perKeyBusy;
  renderPerKeyStrip();
}

function renderPerKeyStrip() {
  if (!els.perKeyStrip) return;
  const active = perKeyTargetActive();
  toggleHidden(els.perKeyStrip, !active);
  if (!active) {
    // Leaving target mode must also drop the picker's slot-target ring state.
    if (els.perKeyVisOff) els.perKeyVisOff.classList.remove("is-active");
    if (els.perKeyVisBreath) els.perKeyVisBreath.classList.remove("is-active");
    // G19A-FIX1 D2: strip hidden -> the original effect subtitle is restored.
    renderColorSubtitle(getColorGroups(getActiveEffectId()));
    return;
  }
  const slots = (state.perKey && state.perKey.slots) || {};
  const entry = slots[String(state.perKeySlot)];
  const vis = entry ? entry.vis : "lit";
  // G19A-FIX1 D2: the strip label AND the picker subtitle render from the
  // same slot state through the canonical pure functions.
  setText(els.perKeyStripLabel, perKeyStripLabel(state.perKeySlot, entry));
  renderColorSubtitle(getColorGroups(getActiveEffectId()));
  if (els.perKeyVisOff) els.perKeyVisOff.classList.toggle("is-active", vis === "off");
  if (els.perKeyVisBreath) els.perKeyVisBreath.classList.toggle("is-active", vis === "breath");
}

// Point the shared picker at the per-key slot's saved color.
function syncPerKeyTargetInputs() {
  const slots = (state.perKey && state.perKey.slots) || {};
  const entry = slots[String(state.perKeySlot)];
  if (entry && Array.isArray(entry.rgb) && entry.rgb.length === 3) {
    syncCustomColorInputs(entry.rgb[0], entry.rgb[1], entry.rgb[2]);
  }
}

function selectPerKeySlot(slot) {
  state.perKeySlot = state.perKeySlot === slot ? null : slot;
  renderKeymapGrid();
  renderPerKeyStrip();
  if (state.perKeySlot == null) return;
  syncPerKeyTargetInputs();
  updateColorControls(getActiveEffectId());
  if (els.colorControlsSection && els.colorControlsSection.scrollIntoView) {
    els.colorControlsSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function writePerKeySlotVis(vis) {
  if (!perKeyTargetActive()) return;
  const slots = (state.perKey && state.perKey.slots) || {};
  const entry = slots[String(state.perKeySlot)];
  // "Turn key OFF" on an already-OFF slot toggles back to Lit (three modes,
  // two chips, per the plan).
  const nextVis = entry && entry.vis === vis ? "lit" : vis;
  const rgb = entry && Array.isArray(entry.rgb) && entry.rgb.length === 3
    ? entry.rgb
    : [state.customColor.red, state.customColor.green, state.customColor.blue];
  await postPerKey("color", { slot: state.perKeySlot, rgb, vis: nextVis });
}

// Bulk writes go slot by slot through the same /api/per-key routes (the API
// has no batch verb on purpose); re-renders are suppressed until the end.
async function runPerKeyBulk(ops) {
  if (state.perKeyBusy) return;
  state.perKeyBusy = true;
  if (els.perKeyResetAll) els.perKeyResetAll.disabled = true;
  if (els.perKeyClearAll) els.perKeyClearAll.disabled = true;
  try {
    for (const op of ops) {
      await postPerKey(op.route, op.value);
    }
  } finally {
    state.perKeyBusy = false;
    await fetchState();
  }
}

function renderEffects(activeEffect) {
  if (!els.effectGrid) return;
  const entries = Object.entries(state.effects || {});
  if (!entries.length) {
    els.effectGrid.innerHTML = "";
    return;
  }

  els.effectGrid.innerHTML = "";
  for (const [id, label] of entries) {
    const button = document.createElement("button");
    button.className = "button button-secondary effect-button";
    button.dataset.effectId = id;
    button.textContent = label;
    button.classList.toggle("is-active", String(activeEffect) === String(id));
    button.addEventListener("click", () => sendAction("set_effect", id));
    els.effectGrid.appendChild(button);
  }
}

if (els.refreshButton) {
  els.refreshButton.addEventListener("click", () => sendAction("refresh"));
}
if (els.flashButton) {
  els.flashButton.addEventListener("click", () => sendAction("device_indication"));
}

// G18 commit 2: Performance panel actions. Each click marks the segmented
// group optimistically; the action response re-renders the whole panel from
// the backend's authoritative state (a rejected value reverts the ring).
if (els.pollingGroup) {
  els.pollingGroup.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-rate]");
    if (!btn) return;
    for (const other of els.pollingGroup.querySelectorAll(".segment")) {
      other.classList.toggle("is-active", other === btn);
    }
    sendAction("set_polling_rate", Number(btn.dataset.rate));
  });
}
if (els.latencyGroup) {
  els.latencyGroup.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-latency]");
    if (!btn) return;
    for (const other of els.latencyGroup.querySelectorAll(".segment")) {
      other.classList.toggle("is-active", other === btn);
    }
    sendAction("set_input_latency", Number(btn.dataset.latency));
  });
}
if (els.sideLightsToggle) {
  els.sideLightsToggle.addEventListener("change", () => {
    sendAction("set_side_lights", els.sideLightsToggle.checked);
  });
}

// G19A: win-lock + per-key actions. Every write goes to a dedicated route;
// none of them touches the device — the store persists, the next lighting
// Apply carries the values to the board.
if (els.winLockToggle) {
  els.winLockToggle.addEventListener("change", () => {
    postPerKey("winLock", els.winLockToggle.checked);
  });
}
if (els.perKeyEnable) {
  els.perKeyEnable.addEventListener("change", async () => {
    const payload = await postPerKey("enable", els.perKeyEnable.checked);
    if (payload.ok && !els.perKeyEnable.checked) {
      // Disabling per-key mode exits Edit mode with it: the grid reverts to
      // the plain remap behavior.
      state.perKeyEdit = false;
      state.perKeySlot = null;
      renderKeymapGrid();
      renderKeymapEditor();
      renderPerKeyPanel();
    }
  });
}
if (els.perKeyBrightness) {
  let perKeyBrightnessTimer = null;
  els.perKeyBrightness.addEventListener("input", () => {
    if (els.perKeyBrightnessValue) {
      setText(els.perKeyBrightnessValue, els.perKeyBrightness.value);
    }
    if (perKeyBrightnessTimer) clearTimeout(perKeyBrightnessTimer);
    perKeyBrightnessTimer = setTimeout(() => {
      postPerKey("brightness", Number(els.perKeyBrightness.value));
    }, 120);
  });
}
if (els.perKeyEditToggle) {
  els.perKeyEditToggle.addEventListener("change", () => {
    state.perKeyEdit = els.perKeyEditToggle.checked;
    if (state.perKeyEdit) {
      // Edit ON closes the remap drawer; the per-key target owns the grid.
      keymap.selectedSlot = null;
    } else {
      // Edit OFF closes the per-key target; drawer behavior is restored.
      state.perKeySlot = null;
    }
    renderKeymapGrid();
    renderKeymapEditor();
    renderPerKeyStrip();
  });
}
if (els.perKeyResetAll) {
  els.perKeyResetAll.addEventListener("click", () => {
    const ops = [];
    for (let slot = 0; slot < 120; slot += 1) {
      ops.push({ route: "color", value: { slot, rgb: [255, 255, 255], vis: "lit" } });
    }
    runPerKeyBulk(ops);
  });
}
if (els.perKeyClearAll) {
  els.perKeyClearAll.addEventListener("click", () => {
    const slots = state.perKey && state.perKey.slots
      ? Object.keys(state.perKey.slots) : [];
    // G19A-FIX1 D4: the destructive bulk removal is gated behind ONE
    // confirm() — it must fire BEFORE any fetch, and cancel aborts
    // silently (no API call, no state change).
    if (!window.confirm(`Remove all ${slots.length} stored key colors?`)) return;
    runPerKeyBulk(slots.map((slot) => ({ route: "clear", value: Number(slot) })));
  });
}
// The strip lives inside the color card: two vis chips (each toggles back to
// Lit when re-clicked) + per-slot clear, all on the pinned routes.
if (els.perKeyVisOff) {
  els.perKeyVisOff.addEventListener("click", () => writePerKeySlotVis("off"));
}
if (els.perKeyVisBreath) {
  els.perKeyVisBreath.addEventListener("click", () => writePerKeySlotVis("breath"));
}
if (els.perKeyClearSlot) {
  els.perKeyClearSlot.addEventListener("click", () => {
    if (!perKeyTargetActive()) return;
    postPerKey("clear", state.perKeySlot);
  });
}

// ---------------------------------------------------------------------------
// Key remapping panel (G7). Talks to GET /api/keystate and the apply-keymap /
// reset-keymap / switch-profile actions. G10: the per-cap slot mapping is
// NO LONGER a `row * stride + column` formula — every cap's slot number is
// read from the row tables of keymap-slots.js. G17 (retracting the G11
// "empirical slot 58" claim): the cap labeled "Caps Lock" binds to slot 60 —
// the vendor Buttoninfo_Default's own CapsLock cell, sweep-confirmed on the
// operator's WB5092 firmware (G13: F13@58 + F14@60 written, a caps press
// emitted 00 00 69 twice on the OS listener, nothing from 58). The head cells
// 55, 56, 57, 58, 59 (factory 0xFF, 58 among them) sit in NO visual row and
// can never bind to a cap. Slots that carry no keycap (NON_KEYCAP,
// per-slot `is_keycap` flag from /api/keystate) render as invisible spacers so
// the 15-column row alignment is preserved — the grid stays 6 x 15 cells.
// ---------------------------------------------------------------------------

const KEYMAP_SLOT_COUNT = 120;
// The slot table ships as a classic script (keymap-slots.js, loaded before
// app.js in index.html). It is the single source of truth for slot binding:
// a missing table must NEVER fall back to an arithmetic guess (that class of
// silent per-row offset IS the G10 defect) — the panel fails closed instead.
const KEYMAP_TABLE = (typeof KEYMAP_SLOT_TABLE !== "undefined" && KEYMAP_SLOT_TABLE)
  ? KEYMAP_SLOT_TABLE : null;
const KEYMAP_COLS = KEYMAP_TABLE ? KEYMAP_TABLE.rows[0].slots.length : 15;
// Offline mirror of app.NON_KEYCAP_SLOTS: matrix artifacts 14/72/92, the
// hidden row-4 head cells 55/56/57/58/59 (G17: slot 58 is one of these dead
// spares — the G11 "CapsLock at 58" claim is retracted; Caps Lock is slot 60
// and renders as a keycap) and the five row-6 `··` spacers 103/104/106/107/111
// (remap/KEYMAP_MAP.md).
// Used only when no /api/keystate payload is loaded yet; the payload's
// per-slot `is_keycap` flag wins.
const KEYMAP_NON_KEYCAP = new Set(KEYMAP_TABLE
  ? KEYMAP_TABLE.non_keycap_slots
  : [14, 55, 56, 57, 58, 59, 72, 92, 103, 104, 106, 107, 111]);
const KEYMAP_GROUP_ORDER = [
  "alpha", "numeric", "fkeys", "modifiers", "nav",
  "keypad", "media", "punct", "editing", "system", "intl", "vendor", "other",
];
// G9 FIX C: human headings for the dropdown group headings (empty query
// shows the FULL grouped list under these).
const KEYMAP_GROUP_LABELS = {
  alpha: "Letters",
  numeric: "Digits row",
  fkeys: "F-keys",
  modifiers: "Modifiers",
  nav: "Navigation",
  keypad: "Numpad",
  media: "Media",
  punct: "Punctuation",
  editing: "Editing",
  system: "System",
  intl: "International",
  vendor: "Vendor specials",
  other: "Other",
};

const keymap = {
  payload: null, // latest /api/keystate payload (catalog + slots + can_switch)
  baseline: null, // codes as read from the device (120)
  buffer: null, // editable copy (120)
  selectedSlot: null,
  busy: false,
  codeMatches: [], // catalog entries the search query currently shows
  codeCursor: 0, // keyboard selection index into codeMatches
};

const km = {
  banner: document.getElementById("keymap-banner"),
  verifyBanner: document.getElementById("keymap-verify-banner"),
  dirtyCounter: document.getElementById("keymap-dirty-counter"),
  refresh: document.getElementById("keymap-refresh"),
  selfTest: document.getElementById("keymap-self-test"),
  selfTestReport: document.getElementById("keymap-selftest-report"),
  profileGrid: document.getElementById("keymap-profile-grid"),
  grid: document.getElementById("keymap-grid"),
  editor: document.getElementById("keymap-editor"),
  editorClose: document.getElementById("keymap-editor-close"),
  editorTitle: document.getElementById("keymap-editor-title"),
  editorNormal: document.getElementById("keymap-editor-normal"),
  editorUnassigned: document.getElementById("keymap-editor-unassigned"),
  editorCurrent: document.getElementById("keymap-editor-current"),
  editorFactory: document.getElementById("keymap-editor-factory"),
  editorType: document.getElementById("keymap-editor-type"),
  search: document.getElementById("keymap-search"),
  codeList: document.getElementById("keymap-code-list"),
  revertKey: document.getElementById("keymap-revert-key"),
  unassignKey: document.getElementById("keymap-unassign-key"),
  editorStaged: document.getElementById("keymap-editor-staged"),
  writeButton: document.getElementById("keymap-write"),
  resetAll: document.getElementById("keymap-reset-all"),
  // G18 commit 3: profile export/import toolbar (local store only).
  exportButton: document.getElementById("keymap-export"),
  importButton: document.getElementById("keymap-import"),
  importFile: document.getElementById("keymap-import-file"),
};

const KEYMAP_BANNER_TEXT = km.banner ? km.banner.textContent.trim() : "";
// G18 P4: fallback banner when the blank mirror is covered by the local
// store's saved state (server sends the same sentence in payload.message).
const KEYMAP_STORE_FALLBACK_TEXT =
  "Device mirror is blank — showing your last saved configuration.";

// --- G12: remap verification + self-healing revert (front end) --------------
// Physics constraint: a remap's effect is observable ONLY through a physical
// keystroke. This front end (a) forwards every keydown to /api/keyevent while
// the page has focus, (b) arms the server-side matcher right after a write,
// and (c) paints the verdict banner. The 60 s timer, the matcher and the
// auto-revert all live server-side (app.py) — the UI cannot forge a VERIFIED.
let verifyPollTimer = null;

function applyVerifyState(verify) {
  if (!km.verifyBanner) return;
  if (!verify || !verify.banner) {
    toggleHidden(km.verifyBanner, true);
    return;
  }
  setText(km.verifyBanner, verify.banner);
  toggleHidden(km.verifyBanner, false);
}

// G16: after EVERY successful matrix write the drawer banner states which
// profile/layer was written and how the operator resolves a profile drift
// (the GUI's software counter can drift from the board's real active
// profile). The SERVER composes the whole sentence
// (payload.keymap_write_banner); the UI only paints it, and only on the OK
// path, after fetchKeystate() — which resets the banner to default.
function paintKeymapWriteBanner(payload) {
  if (!km.banner || !payload || !payload.keymap_write_banner) return;
  setText(km.banner, payload.keymap_write_banner);
  toggleHidden(km.banner, false);
}

async function postVerifyRemap(slot, expect) {
  try {
    const response = await fetch("/api/verify-remap", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot, expect }),
    });
    const payload = await response.json();
    applyVerifyState(payload.verify);
    if (payload.ok && payload.verify && payload.verify.timeout_seconds) {
      // One refetch shortly after the server-side timer expires, so the
      // auto-revert verdict (banner + restored grid) lands without a reload.
      if (verifyPollTimer) window.clearTimeout(verifyPollTimer);
      verifyPollTimer = window.setTimeout(
        () => { fetchKeystate(); },
        payload.verify.timeout_seconds * 1000 + 2000,
      );
    }
    return payload;
  } catch (error) {
    return null; // verification is best-effort; it must never break the UI
  }
}

function firstDirtyKeymapSlot() {
  if (!keymap.buffer || !keymap.baseline) return null;
  for (let slot = 0; slot < KEYMAP_SLOT_COUNT; slot += 1) {
    if (keymap.buffer[slot] !== keymap.baseline[slot]) return slot;
  }
  return null;
}

async function postKeyEvent(code, key) {
  try {
    const response = await fetch("/api/keyevent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, key }),
    });
    if (!response.ok) return;
    const payload = await response.json();
    applyVerifyState(payload.verify);
  } catch (error) {
    // The logger is fire-and-forget: a lost event only means "no input seen"
    // server-side, which self-heals (auto-revert), never corrupts the UI.
  }
}

function keymapCodes() {
  return (keymap.payload && keymap.payload.codes) || [];
}

function keymapCodeName(code) {
  const entry = keymapCodes().find((item) => item.code === code);
  if (entry) return entry.name;
  return keycodeHex(code); // G18 F1: numeric conversion, never String(code)
}

function keymapCodeShort(code) {
  if (code === 0xFF) return "—";
  return keymapCodeName(code).replace(/ \(0x[0-9A-Fa-f]{2}\)$/, "");
}

function keymapData() {
  return (keymap.payload && keymap.payload.keymap) || null;
}

function keymapSlotMeta(slot) {
  const data = keymapData();
  if (!data) return null;
  return data.slots.find((item) => item.slot === slot) || null;
}

function keymapDirtyCount() {
  if (!keymap.buffer || !keymap.baseline) return 0;
  let count = 0;
  for (let slot = 0; slot < KEYMAP_SLOT_COUNT; slot += 1) {
    if (keymap.buffer[slot] !== keymap.baseline[slot]) count += 1;
  }
  return count;
}

function keymapIsEditable() {
  return Boolean(keymap.buffer) && !keymap.busy;
}

async function fetchKeystate() {
  try {
    const response = await fetch("/api/keystate");
    applyKeystate(await response.json());
  } catch (error) {
    showKeymapProblem(`Failed to load keymap state: ${error}`);
  }
}

function showKeymapProblem(message) {
  setText(km.banner, message);
  toggleHidden(km.banner, false);
  renderKeymapMeta();
  renderKeymapProfiles();
  renderKeymapGrid();
  renderKeymapEditor();
}

function applyKeystate(payload) {
  keymap.payload = payload;
  applyVerifyState(payload.verify); // G12: keep the verdict banner in sync
  const data = keymapData();
  if (!data) {
    showKeymapProblem(payload.message || "Keymap unavailable.");
    return;
  }
  if (km.banner) km.banner.textContent = KEYMAP_BANNER_TEXT;
  if (data.source === "store") {
    // G18 P4: the blank-mirror fallback is on screen — the banner must say
    // "last saved configuration", never the factory-default sentence.
    km.banner.textContent = payload.message || KEYMAP_STORE_FALLBACK_TEXT;
  }
  toggleHidden(km.banner, !data.is_dirty_source);
  keymap.baseline = data.slots.map((slot) => slot.code);
  keymap.buffer = keymap.baseline.slice();
  keymap.selectedSlot = null;
  keymap.busy = false;
  renderKeymapMeta();
  renderKeymapProfiles();
  renderKeymapGrid();
  renderKeymapEditor();
}

function renderKeymapMeta() {
  const count = keymapDirtyCount();
  if (keymap.busy) {
    setText(km.dirtyCounter, "Writing to keyboard…");
    setText(km.writeButton, "Writing…");
    if (km.writeButton) km.writeButton.disabled = true;
    toggleHidden(km.writeButton, false);
    return;
  }
  setText(km.dirtyCounter, `${count} unsaved change${count === 1 ? "" : "s"}`);
  // G9 FIX E: the write button only exists while there is something to write,
  // and its label is grammatical (never "N key(s)").
  setText(km.writeButton, `Write ${count} key${count === 1 ? "" : "s"} to keyboard`);
  toggleHidden(km.writeButton, count === 0);
  if (km.writeButton) km.writeButton.disabled = count === 0 || !keymapIsEditable();
}

function renderKeymapProfiles() {
  if (!km.profileGrid) return;
  km.profileGrid.innerHTML = "";
  const data = keymapData();
  const options = (data && data.can_switch) || [];
  for (const option of options) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "keymap-profile-card";
    card.classList.toggle("is-active", Boolean(option.active));
    card.disabled = !option.active && !keymapIsEditable();
    card.title = option.active ? "Active profile/layer" : `${option.hint || "switch-profile first"}: the device mirror only exposes the active profile`;
    card.innerHTML = `
      <strong>Profile ${option.profile}</strong>
      <span>Layer ${option.layer}</span>
      ${option.active ? '<em class="keymap-active-tag">active</em>' : ""}
    `;
    card.addEventListener("click", () => switchKeymapProfile(option.profile, option.layer));
    km.profileGrid.appendChild(card);
  }
}

function renderKeymapGrid() {
  if (!km.grid) return;
  km.grid.innerHTML = "";
  if (!KEYMAP_TABLE) {
    showKeymapProblem("Keymap slot table (keymap-slots.js) failed to load — no "
      + "keycap is bound to any slot, so the grid stays empty. Reload the app.");
    return;
  }
  // G10 + G17: iterate the KEYMAP_MAP.md row tables + its SWEEP-VERDICT note —
  // every cap's slot number is a table cell, not a formula. The cap labeled
  // "Caps Lock" binds to slot 60 (the vendor table's CapsLock cell, confirmed
  // by the G13 sweep) by construction, and slots in no visual row (55-59,
  // 75-79, 95-99, ...) can never bind to a cap.
  for (const row of KEYMAP_TABLE.rows) {
    for (let column = 0; column < row.slots.length; column += 1) {
      const slot = row.slots[column];
      const capName = row.keys[column];
      const meta = keymapSlotMeta(slot);
      // G9 FIX A: per-slot is_keycap from the payload (source of truth),
      // mirrored offline by the table's null key name / KEYMAP_NON_KEYCAP. A
      // slot without a keycap is an INVISIBLE spacer: no border/background/
      // label, not clickable, not tabbable — it only preserves the 15-column
      // row alignment. Raw "#NNN" names never render anywhere.
      const noKeycap = meta
        ? meta.is_keycap === false
        : (capName === null || KEYMAP_NON_KEYCAP.has(slot));
      if (noKeycap) {
        const spacer = document.createElement("div");
        spacer.className = "keycap-spacer";
        spacer.setAttribute("aria-hidden", "true");
        km.grid.appendChild(spacer);
        continue;
      }
      const code = keymap.buffer ? keymap.buffer[slot] : (meta ? meta.code : 0xFF);
      const dirty = Boolean(keymap.buffer && keymap.baseline
        && keymap.buffer[slot] !== keymap.baseline[slot]);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "keycap";
      button.classList.toggle("is-dirty", dirty);
      // G19A: while per-key Edit mode owns the grid the selection ring marks
      // the COLOR target; otherwise (Edit off / per-key off) it keeps the
      // remap-drawer selection — never both at once.
      const perKeyMode = perKeyClickOpensColorPanel(
        state.perKey && state.perKey.enabled,
        state.perKeyEdit,
      );
      button.classList.toggle(
        "is-selected",
        perKeyMode ? state.perKeySlot === slot : keymap.selectedSlot === slot,
      );
      button.dataset.slot = String(slot);
      // G18-FIX1 D1: the face shows the compact label ("LCtrl"); the tooltip
      // keeps the FULL name ("Left Ctrl") — G9 FIX B behaviour unchanged.
      const fullName = (meta && meta.physical) || capName || "";
      const labelEl = document.createElement("span");
      labelEl.className = "keycap-label";
      labelEl.textContent = keycapFaceLabel(code, meta && meta.physical, capName);
      labelEl.title = fullName; // G9 FIX B: native tooltip with the full name
      const codeEl = document.createElement("span");
      codeEl.className = "keycap-code";
      // G18 F2: compact labels for dense caps (modifiers, cluster keys);
      // everything else keeps the catalog name. Tooltip/editor untouched.
      codeEl.textContent = keycapShortLabel(code) || keymapCodeShort(code);
      button.append(labelEl, codeEl);
      // G19A: saved per-key colors tint the cap's BOTTOM FACE (a 2px inset
      // bar via CSS ::after) — the label stays fully readable, and OFF slots
      // carry no tint. The tint is our stored value, never a device read.
      const tint = perKeyTintRgb(state.perKey, slot);
      if (tint) {
        button.classList.add("has-perkey-color");
        button.style.setProperty("--perkey-tint", rgbToHex(tint[0], tint[1], tint[2]));
      }
      // G15: the tooltip shows the EFFECTIVE type the firmware honors
      // (payload type_name), and flags the cells whose type activates them.
      button.title = `Slot ${slot} · ${keymapCodeName(code)}`
        + (meta ? ` · ${meta.type_name ?? meta.code_type_label}` : "")
        + (meta && meta.cell_active ? " · cell active" : "")
        + (dirty ? " · unsaved change" : "")
        // G19A: name the bottom-face tint explicitly — it is a locally
        // stored per-key color, not something read off the keyboard.
        + (tint ? " · per-key color (stored in this profile)" : "");
      button.addEventListener("click", () => {
        // G19A: while per-key Edit mode is on, the click opens the color
        // panel for this slot instead of the remap drawer (mutually
        // exclusive — Edit off restores the drawer click exactly).
        if (perKeyClickOpensColorPanel(state.perKey && state.perKey.enabled, state.perKeyEdit)) {
          selectPerKeySlot(slot);
        } else {
          selectKeymapSlot(slot);
        }
      });
      km.grid.appendChild(button);
    }
  }
}

function renderKeymapCodeList() {
  if (!km.codeList) return;
  const query = km.search && km.search.value ? km.search.value : "";
  const entries = keymapCodes().filter((entry) => keyEntryMatches(entry, query));
  keymap.codeMatches = entries;
  keymap.codeCursor = clamp(keymap.codeCursor, 0, Math.max(entries.length - 1, 0));
  km.codeList.innerHTML = "";
  if (!entries.length) {
    const empty = document.createElement("p");
    empty.className = "keymap-code-empty";
    empty.textContent = `No remap target matches “${query.trim()}”.`;
    km.codeList.appendChild(empty);
    return;
  }
  const byGroup = new Map();
  for (const entry of entries) {
    if (!byGroup.has(entry.group)) byGroup.set(entry.group, []);
    byGroup.get(entry.group).push(entry);
  }
  const current = keymap.selectedSlot != null && keymap.buffer
    ? keymap.buffer[keymap.selectedSlot] : null;
  let index = 0;
  let cursorEl = null;
  for (const group of KEYMAP_GROUP_ORDER.filter((name) => byGroup.has(name))) {
    const heading = document.createElement("p");
    heading.className = "keymap-code-group";
    heading.textContent = KEYMAP_GROUP_LABELS[group] || group;
    km.codeList.appendChild(heading);
    for (const entry of byGroup.get(group)) {
      const item = document.createElement("div");
      item.className = "keymap-code-item";
      item.setAttribute("role", "option");
      item.dataset.code = String(entry.code);
      item.dataset.index = String(index);
      const isCurrent = entry.code === current;
      item.classList.toggle("is-current", isCurrent);
      item.setAttribute("aria-selected", isCurrent ? "true" : "false");
      if (index === keymap.codeCursor) {
        item.classList.add("is-cursor");
        cursorEl = item;
      }
      const nameEl = document.createElement("span");
      nameEl.className = "keymap-code-name";
      nameEl.textContent = entry.name.replace(/ \(0x[0-9A-Fa-f]{2}\)$/, "");
      const hexEl = document.createElement("span");
      hexEl.className = "keymap-code-hex";
      hexEl.textContent = keycodeHex(entry.code); // G18 F1
      item.append(nameEl, hexEl);
      item.title = entry.aliases && entry.aliases.length
        ? `${entry.name} — search: ${entry.aliases.join(", ")}`
        : entry.name;
      km.codeList.appendChild(item);
      index += 1;
    }
  }
  if (cursorEl) cursorEl.scrollIntoView({ block: "nearest" });
}

function moveKeymapCursor(delta) {
  const count = keymap.codeMatches.length;
  if (!count) return;
  keymap.codeCursor = clamp(keymap.codeCursor + delta, 0, count - 1);
  renderKeymapCodeList();
}

// Stage an assignment: dirty dot + "N unsaved changes" + write button all
// update immediately through setBufferCode (nothing is written to the device
// until the operator presses the write button).
function stageKeymapCode(code) {
  if (!Number.isInteger(code)) return;
  setBufferCode(code);
}

function renderKeymapEditor() {
  const slot = keymap.selectedSlot;
  if (slot == null || !keymap.buffer || !keymapIsEditable()) {
    toggleHidden(km.editor, true);
    return;
  }
  toggleHidden(km.editor, false);
  const meta = keymapSlotMeta(slot);
  setText(km.editorTitle, `${(meta && meta.physical) || "Keycap"} · slot ${slot}`);
  // G10 FIX 4 + G17: a factory-0xFF slot is "unassigned" per the VENDOR table.
  // Keycap presence answers from the payload's is_keycap (grid and
  // /api/keystate agree), mirrored offline by the slot table — a real keycap
  // always gets the normal editor (Caps Lock heads row 4 at slot 60, factory
  // byte 0x39), and the hidden head cells 55-59 have no cap at all, so the
  // honest unassigned note remains the backstop for anything keycap-less.
  const slotHasKeycap = meta
    ? meta.is_keycap !== false
    : !KEYMAP_NON_KEYCAP.has(slot);
  const unassigned = keycapSlotIsUnassigned(meta, slotHasKeycap);
  toggleHidden(km.editorUnassigned, !unassigned);
  toggleHidden(km.editorNormal, unassigned);
  if (unassigned) return;
  setText(km.editorCurrent, keymapCodeName(keymap.buffer[slot]));
  setText(km.editorFactory, meta ? keymapCodeName(meta.factory_code) : "—");
  setText(km.editorType, meta ? meta.code_type_label : "—");
  renderKeymapCodeList();
  if (km.revertKey) km.revertKey.disabled = !meta;
  if (km.unassignKey) km.unassignKey.disabled = false;
  // G18 F7: while THIS cap has a staged (dirty) code, name it right above
  // the commit row so the operator sees what Apply will write. Hidden when
  // nothing is staged — it reuses the existing buffer-vs-baseline state.
  const staged = Boolean(keymap.baseline) && keymap.buffer[slot] !== keymap.baseline[slot];
  if (staged) {
    setText(km.editorStaged,
      `Staged: ${keymapCodeName(keymap.buffer[slot])} — press Apply to write it to the keyboard.`);
  }
  toggleHidden(km.editorStaged, !staged);
}

// G10 FIX 5: matrix self-test aid — DEFAULT OFF. Runs ONLY on this button
// click; never on page load, never on any other action. The backend handler
// is read-only (config-mirror GET), so no write path is reachable from here.
async function runMatrixSelfTest() {
  if (!km.selfTest || keymap.busy) return;
  km.selfTest.disabled = true;
  setText(km.selfTest, "Scanning…");
  try {
    const payload = await postAction("matrix-self-test");
    if (!km.selfTestReport) return;
    if (payload.ok && Array.isArray(payload.self_test)) {
      km.selfTestReport.textContent = payload.self_test.join("\n");
    } else {
      km.selfTestReport.textContent = payload.message || "Matrix self-test unavailable.";
    }
    toggleHidden(km.selfTestReport, false);
  } catch (error) {
    if (km.selfTestReport) {
      km.selfTestReport.textContent = `Matrix self-test failed: ${error}`;
      toggleHidden(km.selfTestReport, false);
    }
  } finally {
    km.selfTest.disabled = false;
    setText(km.selfTest, "Run matrix self-test");
  }
}

function selectKeymapSlot(slot) {
  if (!keymapIsEditable()) return;
  keymap.selectedSlot = keymap.selectedSlot === slot ? null : slot;
  keymap.codeCursor = 0;
  renderKeymapGrid();
  renderKeymapEditor();
}

function setBufferCode(code) {
  const slot = keymap.selectedSlot;
  if (slot == null || !keymap.buffer) return;
  keymap.buffer[slot] = code;
  renderKeymapGrid();
  renderKeymapEditor();
  renderKeymapMeta();
}

async function writeKeymapBuffer() {
  const count = keymapDirtyCount();
  if (!keymapIsEditable() || count === 0) return;
  // G12: snapshot the first dirty slot + its target code BEFORE the write
  // resets the baseline, so the matcher arms against the code we just wrote.
  const verifySlot = firstDirtyKeymapSlot();
  const verifyExpect = verifySlot == null ? null : [keymap.buffer[verifySlot]];
  keymap.busy = true;
  renderKeymapMeta();
  renderKeymapProfiles();
  renderKeymapEditor();
  const payload = await postAction("apply-keymap", { codes: keymap.buffer.slice() });
  keymap.busy = false;
  if (payload.ok) {
    await fetchKeystate();
    paintKeymapWriteBanner(payload); // G16: after the resettable keystate repaint
    if (verifySlot != null && verifyExpect && verifyExpect.length) {
      await postVerifyRemap(verifySlot, verifyExpect);
    }
  } else {
    renderKeymapMeta();
  }
}

async function resetKeymapActiveProfile() {
  const data = keymapData();
  if (!data || !keymapIsEditable()) return;
  const okToWrite = window.confirm(
    `Reset profile ${data.profile} layer ${data.layer} to the factory default keymap?`
    + " This writes the full 120-slot matrix to the keyboard.",
  );
  if (!okToWrite) return;
  keymap.busy = true;
  renderKeymapMeta();
  const payload = await postAction("reset-keymap", null);
  keymap.busy = false;
  if (payload.ok) {
    await fetchKeystate();
    paintKeymapWriteBanner(payload); // G16: the reset names its profile too
  } else {
    renderKeymapMeta();
  }
}

async function switchKeymapProfile(profile, layer) {
  if (!keymapIsEditable()) return;
  keymap.busy = true;
  renderKeymapMeta();
  const payload = await postAction("switch-profile", { profile, layer });
  keymap.busy = false;
  if (payload.ok) {
    await fetchKeystate();
  } else {
    renderKeymapMeta();
  }
}

if (km.refresh) km.refresh.addEventListener("click", () => fetchKeystate());
if (km.selfTest) km.selfTest.addEventListener("click", () => runMatrixSelfTest());
if (km.writeButton) km.writeButton.addEventListener("click", () => writeKeymapBuffer());
if (km.resetAll) km.resetAll.addEventListener("click", () => resetKeymapActiveProfile());

// --- G18 commit 3: profile export / import (local store only) ---------------
// Export downloads the STORED profile/layer as a JSON attachment — the
// server reads the local store, never the device. Import POSTs the file for
// server-side validation into the store; on success the codes are staged
// into the editor buffer (dirty counter rises, Write button appears). The
// device is touched ONLY by the existing gated Write path.
async function exportActiveProfile() {
  const data = keymapData();
  const profile = data ? data.active_profile : 1;
  const layer = data ? data.active_layer : 1;
  try {
    const response = await fetch(`/api/export?profile=${profile}&layer=${layer}`);
    if (!response.ok) {
      const err = await response.json().catch(() => null);
      setText(els.statusMessage, (err && err.message) || "Export failed.");
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `open-glorious-core-p${profile}-l${layer}.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    setText(els.statusMessage, `Exported profile ${profile} layer ${layer} — check your downloads.`);
  } catch (error) {
    setText(els.statusMessage, `Export failed: ${error}`);
  }
}

async function importProfileFile(file) {
  let payload;
  try {
    payload = JSON.parse(await file.text());
  } catch (error) {
    setText(els.statusMessage, "Import failed: file is not valid JSON.");
    return;
  }
  try {
    const response = await fetch("/api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!result.ok) {
      // Invalid file: error toast, NOTHING staged (server staged nothing).
      setText(els.statusMessage, result.message || "Import rejected.");
      return;
    }
    const data = keymapData();
    const targetsActive = data
      && Number(result.profile) === Number(data.active_profile)
      && Number(result.layer) === Number(data.active_layer);
    if (targetsActive && keymap.buffer) {
      for (const [slotKey, code] of Object.entries(result.keymap || {})) {
        const slot = Number(slotKey);
        if (Number.isInteger(slot) && slot >= 0 && slot < KEYMAP_SLOT_COUNT) {
          keymap.buffer[slot] = Number(code);
        }
      }
    }
    setText(els.statusMessage, result.message);
    renderKeymapMeta();
    renderKeymapGrid();
    renderKeymapEditor();
  } catch (error) {
    setText(els.statusMessage, `Import failed: ${error}`);
  }
}

if (km.exportButton) {
  km.exportButton.addEventListener("click", () => exportActiveProfile());
}
if (km.importButton && km.importFile) {
  km.importButton.addEventListener("click", () => km.importFile.click());
  km.importFile.addEventListener("change", () => {
    const file = km.importFile.files && km.importFile.files[0];
    km.importFile.value = "";
    if (file) importProfileFile(file);
  });
}
if (km.revertKey) {
  km.revertKey.addEventListener("click", () => {
    const meta = keymap.selectedSlot == null ? null : keymapSlotMeta(keymap.selectedSlot);
    if (meta) setBufferCode(meta.factory_code);
  });
}
if (km.unassignKey) km.unassignKey.addEventListener("click", () => setBufferCode(0xFF));
if (km.search) {
  km.search.addEventListener("input", () => {
    keymap.codeCursor = 0;
    renderKeymapCodeList();
  });
  // G9 FIX C: the search input KEEPS FOCUS while ArrowDown/ArrowUp move the
  // list selection (typing is never interrupted); Enter stages the selected
  // target immediately.
  km.search.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveKeymapCursor(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveKeymapCursor(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const entry = keymap.codeMatches[keymap.codeCursor];
      if (entry) stageKeymapCode(entry.code);
    }
  });
}
if (km.codeList) {
  // Click/tap a result to stage it; focus returns to the search box so the
  // operator can keep refining the query. mousedown (not click) +
  // preventDefault keeps the caret in the input the whole time.
  km.codeList.addEventListener("mousedown", (event) => {
    const item = event.target.closest(".keymap-code-item");
    if (!item) return;
    event.preventDefault();
    const code = Number.parseInt(item.dataset.code, 10);
    if (!Number.isNaN(code)) stageKeymapCode(code);
    if (km.search) km.search.focus();
  });
  km.codeList.addEventListener("mousemove", (event) => {
    const item = event.target.closest(".keymap-code-item");
    if (!item) return;
    const index = Number.parseInt(item.dataset.index, 10);
    if (Number.isNaN(index) || index === keymap.codeCursor) return;
    keymap.codeCursor = index;
    const current = km.codeList.querySelector(".keymap-code-item.is-cursor");
    if (current) current.classList.remove("is-cursor");
    item.classList.add("is-cursor");
  });
}

// G14 FIX 1: the drawer is a width-zero OVERLAY on the grid — dismissing it
// (deselect the cap; renderKeymapEditor then hides the panel) is its only
// affordance, so it must be reachable without hunting: the head close button
// and the Escape key.
function closeKeymapDrawer() {
  if (keymap.selectedSlot == null) return;
  keymap.selectedSlot = null;
  renderKeymapGrid();
  renderKeymapEditor();
}
if (km.editorClose) km.editorClose.addEventListener("click", () => closeKeymapDrawer());
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeKeymapDrawer();
});

// G14 FIX 3: boot at the TOP of the page. The web view restores the previous
// mid-page scroll on launch (swatch row clipped under the header, scrollbar
// thumb mid-trough), so pin the document (window.scrollTo, per spec) AND the
// app's real scroll box (.workspace-shell) to the top: at boot, on the next
// animation frame (runs after layout, before the user sees the restored
// position), and once more on the window load event.
function scrollAppToTop() {
  window.scrollTo(0, 0);
  const shell = document.querySelector(".workspace-shell");
  if (shell) shell.scrollTop = 0;
}
scrollAppToTop();
window.requestAnimationFrame(scrollAppToTop);
window.addEventListener("load", scrollAppToTop);

fetchState().catch((error) => {
  setText(els.statusMessage, `Failed to load state: ${error}`);
  setText(els.statusPill, "Error");
});
fetchKeystate();

// G12 item 2: keydown logger. Every keydown while this page has focus is
// forwarded to the localhost /api/keyevent as {code, key}; the server-side
// armed matcher (app.py) is the ONLY judge of a remap verdict. Nothing is
// stored server-side beyond the armed pending, events seen while nothing is
// armed are dropped, and a lost event self-heals (auto-revert) — so this
// listener must never throw into the UI.
document.addEventListener("keydown", (event) => {
  if (!event || !event.code) return;
  postKeyEvent(event.code, event.key);
});
