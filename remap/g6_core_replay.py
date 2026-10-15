#!/usr/bin/env python3
"""G6 — one-shot faithful replay of Glorious CORE 2.1.22 connect sequence.

Safety:
  * All content is FACTORY: default types + default codes from packaged data,
    zero macro buffers, factory-brightness LED settings.
  * Each report is sent EXACTLY ONCE in CORE's own order. No retries, no sweeps.
  * Keyboard remains usable throughout (writes are factory-over-factory).
Sequence per GmmkSeries.InitialDevice/SetProfileDataFromDB (PROTOCOL_DECODE.md):
  SetPollingRatetoDevice(1000, EP2=1, LEDNoChange=1)
  per slot 1..3 profile x 1..3 layer:
      SendButtonMatrix2Device   cmd 0x03  types[8..127] codes[128..247]
      SendMacroType2Device      cmd 0x04  types block (zeros = no macros)
      SendAlrGR2Device          cmd 0x09  altgr block (zeros = none)
      SetLEDEffect:             slot(1,1) cmd 0x02 live effect;
                                others    cmd 0x07 settings (brightness only)
  SetMacroFunction: skipped (macro list empty -> CORE callback w/o sends)
  ChangeProfileID cmd 0x01 -> (1,1)
  final SetPollingRatetoDevice cmd 0x08 {1000, EP2=1, LEDNoChange=0}
"""
import sys, time
sys.path.insert(0, "src")
from open_glorious_core.backend import StockGmmkProBackend

b = StockGmmkProBackend()
CODES = open("src/open_glorious_core/data/default_keymap.bin", "rb").read()
TYPES = open("src/open_glorious_core/data/default_types.bin", "rb").read()
assert len(CODES) == 120 and len(TYPES) == 120

def z256():
    return bytearray(256)

def send(buf, label):
    b._send_feature_report(bytearray(buf))
    print(f"  sent {label}", flush=True)
    time.sleep(0.15)

print("== G6 CORE connect replay — factory content, exact-once each ==", flush=True)

# 1) opening polling command
buf = z256(); buf[0] = 0x07; buf[1] = 0x08
buf[8] = 0x00          # 1000 Hz
buf[9] = 0x01          # EP2Enable
buf[10] = 0x01         # LEDNoChange = 1 (opening)
send(buf, "0x08 polling open {1000Hz, EP2=1, LEDNoChange=1}")

for profile in range(1, 4):
    for layer in range(1, 4):
        slot = f"({profile},{layer})"
        # key matrix
        buf = z256(); buf[0] = 0x07; buf[1] = 0x03
        buf[2] = profile; buf[3] = layer
        buf[8:128] = TYPES
        buf[128:248] = CODES
        send(buf, f"0x03 matrix {slot}")
        # macro types (all zero)
        buf = z256(); buf[0] = 0x07; buf[1] = 0x04
        buf[2] = profile; buf[3] = layer
        send(buf, f"0x04 macro-types(zero) {slot}")
        # altgr (all zero)
        buf = z256(); buf[0] = 0x07; buf[1] = 0x09
        buf[2] = profile; buf[3] = layer
        send(buf, f"0x09 altgr(zero) {slot}")
        # LED
        if (profile, layer) == (1, 1):
            buf = z256(); buf[0] = 0x07; buf[1] = 0x02
            buf[2] = 0x04          # effect id 4 (Spiraling Wave)
            buf[9] = 0x14          # brightness 20
            send(buf, f"0x02 LED live {slot} effect=4")
        else:
            buf = z256(); buf[0] = 0x07; buf[1] = 0x07
            buf[2] = profile; buf[3] = layer
            buf[8] = 0x14          # brightness 20
            send(buf, f"0x07 LED settings {slot} brightness=20")

# macro data: CORE sends nothing when macro list empty (SetMacroFunction -> immediate callback)

# bind active profile
buf = z256(); buf[0] = 0x07; buf[1] = 0x01
buf[2] = 0x01; buf[3] = 0x01
send(buf, "0x01 ChangeProfile (1,1)")

# final polling command (LEDNoChange = 0)
buf = z256(); buf[0] = 0x07; buf[1] = 0x08
buf[8] = 0x00; buf[9] = 0x01; buf[10] = 0x00
send(buf, "0x08 polling final {1000Hz, EP2=1, LEDNoChange=0}")

print("== replay complete — reading mirror ==", flush=True)
time.sleep(0.5)
from remap.smoke_setkeymap import read_mirror_raw
r = read_mirror_raw(b, 1, 1)
print("mirror head:", r[:8].hex(" "), "nonzero[4:]:", sum(1 for x in r[4:] if x))
from open_glorious_core.backend import locate_codes_table
off = locate_codes_table(r)
print(f"codes table @0x{off:02x}: {r[off:off+16].hex(' ')}")
