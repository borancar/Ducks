#!/usr/bin/env python3
"""
Attribute each relocation write to the instruction that performed it.

Both the differential and the write-log agree on 709 relocated words, yet
DIET's visible relocation loop is seeded with `mov cx,0x2c3` (707). Record the
CS:IP responsible for every write into the image so we can see exactly which
code applied which relocation, and where the extra 2 come from.
"""
import struct
from collections import Counter
from unicorn import *
from unicorn.x86_const import *
from unpack_ducks import Unpacker

SEG = 0x0110
data = open("../Ducks.exe", "rb").read()

up = Unpacker(data, SEG)
attributed = []          # (cs, ip, addr, size, value)


def on_write(uc, access, address, size, value, user):
    attributed.append((uc.reg_read(UC_X86_REG_CS),
                       uc.reg_read(UC_X86_REG_IP),
                       address, size, value))


up.uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
regs = up.run()

image_size = (regs["ss"] - SEG) * 16 + regs["sp"]
base = up.load_base

# Group the writing instructions by (cs:ip) over the final 2000 writes,
# which is comfortably more than the relocation phase needs.
tail = attributed[-2000:]
by_site = Counter((cs, ip) for cs, ip, a, s, v in tail)
print("write sites in the final 2000 writes (cs:ip -> count):")
for (cs, ip), n in by_site.most_common():
    print(f"  {cs:04x}:{ip:04x}  n={n}")

# Now isolate writes into the image performed by each site.
print("\nper-site writes landing inside the image:")
sites = {}
for cs, ip, a, s, v in attributed:
    off = a - base
    if 0 <= off < image_size and s == 2:
        sites.setdefault((cs, ip), []).append(off)
for (cs, ip), offs in sorted(sites.items(), key=lambda kv: -len(kv[1])):
    print(f"  {cs:04x}:{ip:04x}  {len(offs)} word writes  "
          f"first={offs[0]:#07x} last={offs[-1]:#07x}")

# Disassemble around the dominant site to confirm it is the reloc loop.
import capstone
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
top_site = max(sites.items(), key=lambda kv: len(kv[1]))[0]
cs_v, ip_v = top_site
lin = cs_v * 16 + ip_v
code = bytes(up.uc.mem_read(lin - 32, 64))
print(f"\ndisassembly around dominant write site {cs_v:04x}:{ip_v:04x}:")
for insn in md.disasm(code, ip_v - 32):
    mark = "  <== writes here" if insn.address == ip_v else ""
    print(f"  {insn.address:04x}: {insn.bytes.hex():<12} "
          f"{insn.mnemonic} {insn.op_str}{mark}")
