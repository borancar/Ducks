#!/usr/bin/env python3
"""
Cross-check the relocation set two independent ways.

The differential method (diff two runs at different load segments) found 709
candidates, but DIET's own relocation loop is `mov cx,0x2c3` = 707 iterations,
each performing exactly one `add es:[bx],bp`. Reconcile the two.

Method A: differential between load segments.
Method B: observe the actual writes DIET's relocation loop performs.
"""
import struct
from unpack_ducks import Unpacker, MZ

SEG_A, SEG_B = 0x0110, 0x0310

data = open("game/Ducks.exe", "rb").read()

runs = {}
for seg in (SEG_A, SEG_B):
    up = Unpacker(data, seg)
    regs = up.run()
    runs[seg] = (up, regs)

up_a, regs_a = runs[SEG_A]
up_b, regs_b = runs[SEG_B]
rel_ss, rel_sp = regs_a["ss"] - SEG_A, regs_a["sp"]
image_size = rel_ss * 16 + rel_sp

img_a = up_a.read_image(image_size)
img_b = up_b.read_image(image_size)

# ---- Method A: differential -------------------------------------------------
delta = SEG_B - SEG_A
diff_set, unexplained = [], []
i = 0
while i < image_size - 1:
    wa = struct.unpack_from("<H", img_a, i)[0]
    wb = struct.unpack_from("<H", img_b, i)[0]
    if wa != wb:
        if (wa + delta) & 0xFFFF == wb:
            diff_set.append(i)
            i += 2
            continue
        unexplained.append((i, wa, wb))
    i += 1
diff_set = set(diff_set)
print(f"Method A (differential)   : {len(diff_set)} candidates")
if unexplained:
    print(f"  unexplained byte diffs  : {len(unexplained)} {unexplained[:6]}")

# ---- Method B: DIET's own relocation writes --------------------------------
# The relocation loop is the last thing before handoff. Walk the write log
# backwards collecting word-sized writes that land inside the image.
base = up_a.load_base
tail = []
for addr, size, val in reversed(up_a.writes):
    if size != 2:
        break
    off = addr - base
    if not (0 <= off < image_size):
        break
    tail.append(off)
tail.reverse()
print(f"Method B (observed writes): {len(tail)} word writes in the final run")
print(f"  DIET loop counter       : 707 (mov cx,0x2c3)")
write_set = set(tail)
print(f"  distinct offsets        : {len(write_set)}")

# ---- Reconcile -------------------------------------------------------------
only_a = sorted(diff_set - write_set)
only_b = sorted(write_set - diff_set)
print(f"\nin differential but not written by reloc loop: {len(only_a)}")
for off in only_a:
    wa = struct.unpack_from("<H", img_a, off)[0]
    wb = struct.unpack_from("<H", img_b, off)[0]
    ctx = img_a[max(0, off - 6):off + 8].hex()
    print(f"  offset {off:#07x}: A={wa:04x} B={wb:04x} "
          f"(B-A={(wb - wa) & 0xFFFF:#x}) ctx={ctx}")
print(f"in reloc loop but not in differential: {len(only_b)}")
for off in only_b[:10]:
    print(f"  offset {off:#07x}")

# Are the extras simply overlapping detections of a neighbouring reloc?
print("\nadjacency check for differential-only offsets:")
for off in only_a:
    near = [o for o in sorted(write_set) if abs(o - off) <= 3]
    print(f"  {off:#07x} -> real relocs within 3 bytes: "
          f"{[hex(n) for n in near]}")
