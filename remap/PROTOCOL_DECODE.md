# GMMK Pro wireless — core protocol decoded from Glorious CORE 2.1.22 (mac bundle)

Negation notice: this document supersedes the Phase-1/`H1` "cmd-1 profile-config write"
hypothesis, which is **refuted**. What follows is read **directly from the vendor's
own TypeScript source**, extracted from the macOS package (same protocol; no Windows,
no USB interception, no device traffic needed to know it).

Vendor app: Glorious CORE 2.1.22 (Inno Setup pkg for Windows; the macOS DMG carried
the unpacked sources — `app.asar` → `src/electron-process/renderer-interop/`). Files:

```
protocol/device/keyboard/GmmkSeries.ts      (GMMK Pro driver class)
protocol/device/keyboard/keyboard.ts        (base keymap/LED field getters)
others/GMMKLocation.ts                      (per-SN layout + Buttoninfo_Default)
```

## Transport
- Vendor interface (Linux hidraw, dev.item with report descriptor
  `06 01 ff 09 01 a1 01 85 07`) — **feature reports only**.
- Report ID `0x07`, buffer 256/264 bytes. Node "special case 264" is Core allocating
  264; on hidraw feature writes we target 256 (descriptor max) — verified sufficient.
- Report layout: `b[0]=0x07`, `b[1]=command`, `b[2]=` selector…, **payload at `[8..]`.

## Commands (from GmmkSeries.ts, line numbers of 2.1.22 mac bundle)
| cmd  | name                  | fields beyond [8]                                                     | Core cite |
|------|-----------------------|-----------------------------------------------------------------------|-----------|
| 0x01 | ChangeProfile         | `[2]=profile+1`, `[3]=layer+1` (nothing else — 4-byte command)        | ChangeProfileID / ChangeProfileA |
| 0x02 | SetLEDEffect (live)   | `[2]=effectType [8]=speed [9]=brightness0-20 [15..17]=RGB`            | SetLEDEffectToDevice |
| 0x03 | KeyMatrix (write!)    | `[2]=profile+1 [3]=layer+1`, **types** `[8..127]`, **codes** `[128..247]` | SendButtonMatrix2Device |
| 0x04 | MacroType             | `[2]=profile+1 [3]=layer+1`, `[8..]=2 bytes per macro slot`           | SendMacroType2Device |
| 0x05 | MacroData             | `[2]=macroID`, `[8..255]=macro events (248 bytes)`                    | SetMacroDataToDevice |
| 0x06 | PerKey LED layout     | `[2]=profile+1 [3]=layer+1 [4]=KeyNumber [5]=page [6]=brightness [8..207]=data` | SetLEDTypeToDevice |
| 0x07 | LED settings/profile  | `[2..7]=profile/layer/effect/winlock/perkey/capslight [8]=brightness [10]=inputLatency [11]=sleep` | SetLEDEffect2Device |
| 0x08 | PollingRate (init)    | `[8]=rateIdx(1000Hz=0) [9]=EP2Enable [10]=LEDNoChange`                | SetPollingRatetoDevice |
| 0x09 | AltGr map             | per-key AltGr assignment                                              | SendAlrGR2Device |
| GET  | Config mirror (our read path, cmd 0x81 / 129) | returns profile/layer + brightness + 120-byte keymap codes block | GetFeatureReport |

## Connect sequence on every Core boot (InitialDevice, GmmkSeries.ts ~105)
```
SetPollingRatetoDevice   cmd 0x08  {iPollingrate:1000, EP2Enable:1, LEDNoChange:1}
SetProfileDataFromDB     for each profile 0..2, layer 0..2:
    SetKeyFunction      cmd 0x03 (Buttoninfo_Default-based matrix)
                        cmd 0x04 (skipped when no macros)
                        cmd 0x09 (always sent, even all-zero buffer)
    SetLEDEffect        cmd 0x02 (live) or cmd 0x07 (settings per profile)
ChangeProfileID         cmd 0x01 {profileindex+1, layer+1}
```

## Why H1 failed
H1 packed keymap bytes into cmd 0x01 — but cmd **0x01 is the 4-byte
profile-switch command**; firmware ignores everything after `[3]`. The real
keymap-write command is `0x03` with a 120-type + 120-code matrix. There is no
auth, no unlock, no "handshake" — the missing piece was the command byte.

## Buttoninfo_Default (GMMKLocation.ts `"0x320F0x5092"`, the wireless Pro)
Flat 240 bytes: `[0..119]` = per-key **types**, `[120..239]` = per-key **codes**.
- Types histogram: 0x01×77 (normal single shot), 0x02×7 (toggle/hold?), 0x03×1
  (consumer = Mute button), 0xff×35 (unused/side-LED slots).
- Codes section == `default_keymap.bin` **byte-for-byte** (our GET-mirror template from
  Phase 1) — cross-validated.

## Type mask semantics (KeyAssignToData)
```
0x01        Normal (single key, one code)
0x03        Consumer/Multimedia/code = consumer ID
0x05        Macro (code = macro ID)
0x07        Launch program
0x08        Keyboard function
0x09        Mouse function
0x07        2 additional bits? (code says 0x07 = launch program)
0xE0+mask   Combination: Ctrl 0, Shift 1, Alt 2, Win 3, FN 4 — sum of bits
0xE1/0xE2   Shift/Ctrl? (search for exact if needed for modifiers)
Disable     DataBuffer[i]=0x01 type , DataBuffer[KML+i]=0x00 code (no code)
```
`KnobAssignToData` looks up 'Knobleft'/'Knobright' — **absent in the wireless SN's
Matrix_LEDCode**, so knob writes are the mute-filter path only; single code 0x03.

## The 68 physical keys (Matrix_KEYButtons, `"0x320F0x5092"`)
Esc F1..F12 Insert Mute / ~1..0-=[ Backspace Home / Tab QWERTYUIOP[]\ PageUp /
CapsLock ASDFGHJKL;' Enter PageDown / Shift ZXCVBNM,./ RShift Up End /
Ctrl LWin Alt Space RAlt fn RCtrl Left Down Right
→ `iIndex` = `Matrix_LEDCode.indexOf(name)` maps to a 0..119 slot (6 rows × 20,
row 0-5 of 20; side LEDs occupy the rest of the 120 slots).

## G6 findings (10-15) — wireless 5092 has NO dongle tunnel
CORE 2.1.22 handles our board via `GmmkSeries` (keyboard.ts:117: "GMMK PRO:
0x320F0x5044 (alternate board: 0x320F0x5092)"). GmmkSeries.SetFeatureReport has
ONLY plain `SetFeatureReport(devId, 0x07, 256/264, buf)` — the 4-segment
F1F2F3/E0E1E2E3 dongle tunnel (report 0x00, 65 B, gated by GetDongleStatus
F7 handshake) exists only in Gmmk3Series (GMMK3/Onix). Not our board.
Also: CORE never READS the keymap back — connect flow pushes DB state only;
GetFeatureReport(0x07) call sites = none in the device flows. The app is the
source of truth; firmware is a slave. ⇒ blank GET mirror post-reset is the
board's uninitialized shadow buffer, NOT proof the writes didn't land.
⇒ physical keypress is the only true verification for slot-scoped writes.
G6 live replay (39 factory reports: 0x08 open, 9×[0x03,0x04,0x09,LED],
0x01 bind, 0x08 close) — all sent once, no errors. User-reported side effect:
backlight went OFF (my hand-built LED cmd lacked the proven color/flags layout;
restored via proven set_effect(4)/set_brightness(20)/set_speed(50) — PROOF the
channel executes cmd 0x02). Key mappings kept working throughout.

## G6 VERDICT (10-15) — WRITE PATH CONFIRMED PHYSICALLY
Live exact-once test: modified matrix (PrtSc slot 13 → F13 0x68) to (1,1),
then cmd 0x01 bind (1,1). User pressed PrtSc → **no longer sends PrtSc**
(write LANDED physically). Reverted with pure factory matrix 0x03 to (1,1)
(codes[13]=0x46) + cmd 0x01 bind.
⇒ cmd 0x03 keymap writes WORK on wireless 5092. GET(0x81) mirror is NOT a
readback path (uninitialized shadow post-reset, stays all-zero regardless of
writes) — physical keypress is the only verification, exactly as CORE does it
(push-only, never reads keymap back). Feature keymap remapping is PROVEN:
Backend.set_keymap_codes() + change_profile() = working implementation.
