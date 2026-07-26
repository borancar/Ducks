#!/usr/bin/env python3
"""
Validate the unpacked executable before drawing any conclusions from it.

The strongest check is the round trip: load the EXE we produced the way DOS
would (apply its relocation table at a load segment) and compare the resulting
memory image byte-for-byte against what DIET's own decompressor produced at
that same segment. If those agree, our reconstruction is faithful.
"""
import struct
import subprocess
import sys
from unpack_ducks import MZ, Unpacker

PACKED = "../Ducks.exe"
UNPACKED = "Ducks.unpacked.exe"
REFERENCE = "../PickEggs.exe"     # same author/toolchain, never packed

ok = True


def check(label, passed, detail=""):
    global ok
    ok = ok and passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}"
          + (f" - {detail}" if detail else ""))


packed = open(PACKED, "rb").read()
unpacked = open(UNPACKED, "rb").read()
reference = open(REFERENCE, "rb").read()
mzu = MZ(unpacked)

print("=== 1. no longer a packed file ===")
ft = subprocess.run(["file", UNPACKED], capture_output=True, text=True).stdout
print(f"  file(1): {ft.strip()}")
check("file(1) reports MS-DOS executable", "MS-DOS executable" in ft)
check("no 'diet' marker present", b"diet" not in unpacked[:64],
      f"header[0x1c:0x20]={unpacked[0x1c:0x20].hex()}")

print("\n=== 2. header sanity ===")
print(MZ(unpacked).describe())
check("image larger than packed input", mzu.image_size > MZ(packed).image_size,
      f"{MZ(packed).image_size} -> {mzu.image_size} "
      f"({mzu.image_size / MZ(packed).image_size:.2f}x)")
check("declared size matches file length",
      mzu.header_size + mzu.image_size == len(unpacked),
      f"{mzu.header_size}+{mzu.image_size} vs {len(unpacked)}")

print("\n=== 3. relocation table ===")
relocs = mzu.relocs()
check("relocation count is 707 (DIET's own loop counter, mov cx,0x2c3)",
      len(relocs) == 707, f"got {len(relocs)}")
bad = [(s, o) for s, o in relocs if s * 16 + o + 2 > mzu.image_size]
check("every relocation lies inside the image", not bad,
      f"{len(bad)} out of range" if bad else
      f"max target {max(s * 16 + o for s, o in relocs):#x} "
      f"< {mzu.image_size:#x}")

print("\n=== 4. plaintext runtime strings (compare against PickEggs.exe) ===")
for s in (b"Borland C++ - Copyright 1991 Borland Intl.",
          b"Divide error",
          b"Abnormal program termination"):
    in_new = s in unpacked
    in_ref = s in reference
    in_packed = s in packed
    check(f"{s.decode()!r}", in_new,
          f"unpacked={in_new} PickEggs={in_ref} packed={in_packed}")

print("\n=== 5. strings that were mangled while compressed ===")
for s in (b"DUCKS fatal error!", b"Out of memory", b"Hit a key",
          b"settings.dat", b"EGGS\\MAIN.EGG", b"GAME-.SG",
          b"ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    check(f"{s.decode()!r} now intact", s in unpacked,
          "was absent/garbled in packed file" if s not in packed else "")

print("\n=== 6. round trip: our EXE vs DIET's own output ===")
# What DIET produced, at load segment 0x0110.
up = Unpacker(packed, 0x0110)
regs = up.run()
size = up.image_extent()
diet_image = up.read_image(size)

# What DOS would build from the EXE we wrote, at the same load segment.
LOAD = 0x0110
img = bytearray(unpacked[mzu.header_size:])
for seg, off in relocs:
    a = seg * 16 + off
    val = struct.unpack_from("<H", img, a)[0]
    struct.pack_into("<H", img, a, (val + LOAD) & 0xFFFF)

check("image sizes agree", len(img) == len(diet_image),
      f"ours={len(img)} diet={len(diet_image)}")
n = min(len(img), len(diet_image))
diffs = [i for i in range(n) if img[i] != diet_image[i]]
check("relocated image is byte-identical to DIET's output", not diffs,
      f"{len(diffs)} differing bytes, first at {diffs[0]:#x}" if diffs
      else f"all {n} bytes match")

check("entry CS:IP matches the handoff DIET jumped to",
      (mzu.cs, mzu.ip) == (regs["cs"] - LOAD, regs["ip"]),
      f"ours={mzu.cs:04x}:{mzu.ip:04x} "
      f"diet={regs['cs'] - LOAD:04x}:{regs['ip']:04x}")
check("stack SS:SP matches the handoff",
      (mzu.ss, mzu.sp) == (regs["ss"] - LOAD, regs["sp"]),
      f"ours={mzu.ss:04x}:{mzu.sp:04x} "
      f"diet={regs['ss'] - LOAD:04x}:{regs['sp']:04x}")

print("\n" + ("=== ALL CHECKS PASSED ===" if ok else "=== FAILURES PRESENT ==="))
sys.exit(0 if ok else 1)
