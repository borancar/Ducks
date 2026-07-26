#!/usr/bin/env python3
"""
Determine the true stored-image size of the original executable.

SS:SP (0x1c72:0x0080 -> 116640) overshoots: in a DOS EXE the stack and BSS live
in memory granted by minalloc and are NOT stored in the file. DIET exploits
exactly that, parking its decompressor stub in the future stack/BSS area so it
cannot clobber the data it is producing.

So we cannot use SS:SP as the file extent. Instead attribute every byte of
memory to the instruction that wrote it LAST, then find where the decompressor's
output actually ends.
"""
import struct
from collections import Counter, defaultdict
from unicorn import *
from unicorn.x86_const import *
from unpack_ducks import Unpacker

SEG = 0x0110
data = open("../Ducks.exe", "rb").read()
up = Unpacker(data, SEG)
base = up.load_base

last_writer = {}          # linear addr -> (cs, ip)
site_range = defaultdict(lambda: [1 << 30, 0])
site_count = Counter()


def on_write(uc, access, address, size, value, user):
    cs = uc.reg_read(UC_X86_REG_CS)
    ip = uc.reg_read(UC_X86_REG_IP)
    site = (cs, ip)
    site_count[site] += size
    r = site_range[site]
    if address < r[0]:
        r[0] = address
    if address + size > r[1]:
        r[1] = address + size
    for a in range(address, address + size):
        last_writer[a] = site


up.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
regs = up.run()

print("all write sites (bytes written, linear range, image-relative range):")
for site, n in site_count.most_common():
    lo, hi = site_range[site]
    print(f"  {site[0]:04x}:{site[1]:04x}  {n:>7} bytes  "
          f"{lo:#08x}..{hi:#08x}  img {lo - base:#08x}..{hi - base:#08x}")

# For each site, the highest address for which it is still the final writer.
print("\nhighest address each site is the LAST writer of (image-relative):")
top_by_site = {}
for a, site in last_writer.items():
    if site not in top_by_site or a > top_by_site[site]:
        top_by_site[site] = a
for site, a in sorted(top_by_site.items(), key=lambda kv: -kv[1]):
    print(f"  {site[0]:04x}:{site[1]:04x}  {a - base:#08x}")

# Walk down from SS:SP: the first address whose final writer is a decompression
# output store marks the end of genuine program image.
ss_extent = (regs["ss"] - SEG) * 16 + regs["sp"]
print(f"\nSS:SP-derived extent: {ss_extent:#x} ({ss_extent})")

reloc_site = max(
    ((s, [a for a in last_writer if last_writer[a] == s])
     for s in site_count),
    key=lambda kv: 0)  # placeholder, computed properly below

# Show what the final writer is for the top of the SS:SP region, descending.
print("\nfinal-writer map descending from the SS:SP extent:")
runs = []
prev = None
for off in range(ss_extent - 1, -1, -1):
    site = last_writer.get(base + off)
    if site != prev:
        runs.append([site, off, off])
        prev = site
    else:
        runs[-1][2] = off
    if len(runs) > 14:
        break
for site, hi, lo in runs:
    tag = f"{site[0]:04x}:{site[1]:04x}" if site else "NEVER WRITTEN"
    print(f"  img {lo:#08x}..{hi:#08x}  ({hi - lo + 1:>6} bytes)  {tag}")
