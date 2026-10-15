const state = {
  colors: [],
  effects: {},
  effectColorGroups: {},
  devices: [],
  customColor: { red: 255, green: 255, blue: 255 },
  picker: { hue: 0, saturation: 0, value: 1 },
  activeColorGroup: null,
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
// Pure, DOM-free drawer logic (G10 FIX 4, G11 refinement). Extracted verbatim
// between these markers and unit-tested headlessly by tests/test_keymap_slots_js.py (node).
// A slot whose FACTORY code is 0xFF is an unassigned matrix slot per the
// VENDOR table: the drawer shows the unassigned note INSTEAD of the normal
// editor for it. G11 exception: `slotHasKeycap === true` means the human-
// confirmed board map puts a physical keycap on this slot — the firmware wins
// over the vendor table (the operator's WB5092 firmware has CapsLock at slot
// 58, whose factory byte is 0xFF; hiding the editor there would make the
// Caps Lock cap un-remappable).
function keycapSlotIsUnassigned(meta, slotHasKeycap) {
  if (!meta) return false;
  if (slotHasKeycap === true) return false;
  return Number(meta.factory_code) === 0xFF;
}
// [PURE-DRAWER-END]

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
    els.customColorHandle.style.left = `${state.picker.saturation * 100}%`;
    els.customColorHandle.style.top = `${(1 - state.picker.value) * 100}%`;
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
function commitCustomColor() {
  if (customColorTimer) clearTimeout(customColorTimer);
  customColorTimer = setTimeout(() => {
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

function updateColorControls(activeEffect) {
  const groups = renderColorGroupTabs(activeEffect);
  const supportsColor = groups.length > 0;

  toggleHidden(els.customColorLayout, !supportsColor);
  toggleHidden(els.colorGridWrap, !supportsColor);

  if (!supportsColor) {
    setText(els.colorPanelTitle, "Not Available For This Effect");
    setText(els.colorSupportMessage, "This effect currently uses the firmware palette and does not expose custom colors in this Linux app yet.");
    state.activeColorGroup = null;
    return;
  }

  setText(els.colorPanelTitle, groups.length > 1 ? "Group Colors" : "Picker, Hex & RGB");
  setText(els.colorSupportMessage, groups.length > 1 ? "Choose a color group tab, then set the color for that part of the effect." : "This effect supports a single custom color.");
  if (!state.activeColorGroup) {
    state.activeColorGroup = groups[0] || null;
  }
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
      syncCustomColorInputs(payload.state.red, payload.state.green, payload.state.blue);
    }
  }

  renderDevices(payload.backend);
  renderDeviceMeta(selectedDevice);
  renderColors();
  renderEffects(activeEffect);
  updateColorControls(activeEffect);
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
    const hidrawLabel = (device.hidraws || []).join(" • ");
    card.innerHTML = `
      <img src="./assets/gmmkpro-selected.svg" alt="GMMK Pro icon">
      <div>
        <strong>${device.name}</strong>
        <span>${hidrawLabel} • ${backendName}</span>
      </div>
    `;
    els.deviceList.appendChild(card);
  });
}

function renderDeviceMeta(device) {
  if (!els.deviceSummary) return;
  const items = [];
  if (device?.hidraws?.length) items.push(`Interfaces: ${device.hidraws.join(", ")}`);
  if (device?.interfaces?.length) items.push(`USB functions: ${device.interfaces.join(", ")}`);
  if (device?.vendor_id && device?.product_id) items.push(`USB: ${device.vendor_id}:${device.product_id}`);
  if (state.colors.length) items.push(`${state.colors.length} color presets`);
  if (Object.keys(state.effects).length) items.push(`${Object.keys(state.effects).length} effects loaded`);
  setHtml(els.deviceSummary, items.map((item) => `<span class="meta-chip">${item}</span>`).join(""));
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
    button.innerHTML = `<span class="color-preview" style="background:${previewMap[color] || "#ddd"}"></span><span>${color}</span>`;
    button.addEventListener("click", () => sendAction("set_color", color));
    els.colorGrid.appendChild(button);
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

// ---------------------------------------------------------------------------
// Key remapping panel (G7). Talks to GET /api/keystate and the apply-keymap /
// reset-keymap / switch-profile actions. G10/G11: the per-cap slot mapping is
// NO LONGER a `row * stride + column` formula — every cap's slot number is
// read from the row tables of keymap-slots.js. G11 empirical-firmware fix:
// the cap labeled "Caps Lock" binds to slot 58 on the operator's WB5092
// firmware (operator-confirmed; the vendor Buttoninfo_Default says 60 and the
// firmware refutes it — the factory vector's 0xFF at 58 killed CapsLock, a
// recovery 0x39@58 restored it). The cells 55, 56, 57, 59 and 60 (the
// vendor-table caps duplicate) have no empirical identity, sit in NO visual
// row, and can never bind to a cap. Slots that carry no keycap (NON_KEYCAP,
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
// hidden row-4 head cells 55/56/57/59 + 60 (G11: empirical CapsLock is slot
// 58; 60 is the vendor-table caps duplicate with no empirical identity) and
// the five row-6 `··` spacers 103/104/106/107/111 (remap/KEYMAP_MAP.md).
// Used only when no /api/keystate payload is loaded yet; the payload's
// per-slot `is_keycap` flag wins.
const KEYMAP_NON_KEYCAP = new Set(KEYMAP_TABLE
  ? KEYMAP_TABLE.non_keycap_slots
  : [14, 55, 56, 57, 59, 60, 72, 92, 103, 104, 106, 107, 111]);
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
  writeButton: document.getElementById("keymap-write"),
  resetAll: document.getElementById("keymap-reset-all"),
};

const KEYMAP_BANNER_TEXT = km.banner ? km.banner.textContent.trim() : "";

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
  return `0x${String(code).toString(16).toUpperCase().padStart(2, "0")}`;
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
  // G10/G11: iterate the KEYMAP_MAP.md row tables + EMPIRICAL-FIRMWARE note —
  // every cap's slot number is a table cell, not a formula. The cap labeled
  // "Caps Lock" binds to slot 58 (the operator-verified CapsLock cell on this
  // firmware) by construction, and slots in no visual row (55, 56, 57, 59, 60,
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
      button.classList.toggle("is-selected", keymap.selectedSlot === slot);
      button.dataset.slot = String(slot);
      const label = (meta && meta.physical) || capName || "";
      const labelEl = document.createElement("span");
      labelEl.className = "keycap-label";
      labelEl.textContent = label;
      labelEl.title = label; // G9 FIX B: native tooltip with the full name
      const codeEl = document.createElement("span");
      codeEl.className = "keycap-code";
      codeEl.textContent = keymapCodeShort(code);
      button.append(labelEl, codeEl);
      // G15: the tooltip shows the EFFECTIVE type the firmware honors
      // (payload type_name), and flags the empirically-alive cells.
      button.title = `Slot ${slot} · ${keymapCodeName(code)}`
        + (meta ? ` · ${meta.type_name ?? meta.code_type_label}` : "")
        + (meta && meta.cell_active ? " · cell active" : "")
        + (dirty ? " · unsaved change" : "");
      button.addEventListener("click", () => selectKeymapSlot(slot));
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
      hexEl.textContent = `0x${String(entry.code).toString(16).toUpperCase().padStart(2, "0")}`;
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
  // G10 FIX 4 + G11: a factory-0xFF slot is "unassigned" per the VENDOR table.
  // Keycap presence answers from the payload's is_keycap (grid and
  // /api/keystate agree), mirrored offline by the slot table — an empirically
  // confirmed keycap (Caps Lock at slot 58, factory byte 0xFF) always gets the
  // normal editor; the hidden cells 55/56/57/59/60 have no cap at all, so the
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
