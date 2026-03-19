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

async function sendAction(action, value = null) {
  const response = await fetch("/api/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, value }),
  });
  const payload = await response.json();
  applyPayload(payload);
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

fetchState().catch((error) => {
  setText(els.statusMessage, `Failed to load state: ${error}`);
  setText(els.statusPill, "Error");
});
