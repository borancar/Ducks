#!/usr/bin/env python3
"""
Static census of the unpacked executable: which interrupts, ports and files
the code actually references.

A linear sweep over 16-bit code desynchronises on data, so every candidate is
confirmed by convergence: we re-disassemble from several earlier offsets and
require independent runs to land exactly on the same instruction boundary.
That filters out `CD xx` byte pairs that merely occur inside data.
"""
import struct
from collections import Counter, defaultdict
import capstone

EXE = "Ducks.unpacked.exe"

# DOS INT 21h functions worth naming in a report.
DOS_FN = {
    0x00: "terminate", 0x01: "read char", 0x02: "write char",
    0x06: "direct console I/O", 0x07: "direct char in", 0x08: "char in",
    0x09: "write string", 0x0B: "check input status", 0x0C: "flush+read",
    0x0E: "select disk", 0x19: "get current disk", 0x1A: "set DTA",
    0x25: "set interrupt vector", 0x2A: "get date", 0x2C: "get time",
    0x2F: "get DTA", 0x30: "get DOS version", 0x33: "get/set break",
    0x35: "get interrupt vector", 0x36: "get free disk space",
    0x38: "get country info", 0x39: "mkdir", 0x3A: "rmdir", 0x3B: "chdir",
    0x3C: "CREATE file", 0x3D: "OPEN file", 0x3E: "close file",
    0x3F: "READ file", 0x40: "WRITE file", 0x41: "DELETE file",
    0x42: "seek", 0x43: "get/set attributes", 0x44: "ioctl",
    0x47: "get current dir", 0x48: "allocate memory", 0x49: "free memory",
    0x4A: "resize memory", 0x4B: "EXEC program", 0x4C: "terminate with code",
    0x4E: "find first", 0x4F: "find next", 0x56: "RENAME file",
    0x57: "get/set file date", 0x5B: "create new file",
    0x62: "get PSP", 0x68: "commit file",
}

INT_MEANING = {
    0x10: "BIOS video", 0x11: "BIOS equipment", 0x12: "BIOS memory size",
    0x13: "BIOS RAW DISK I/O", 0x14: "BIOS serial", 0x15: "BIOS misc",
    0x16: "BIOS keyboard", 0x17: "BIOS printer", 0x1A: "BIOS clock",
    0x20: "DOS terminate", 0x21: "DOS services", 0x22: "DOS terminate addr",
    0x23: "DOS Ctrl-Break", 0x24: "DOS critical error",
    0x27: "DOS TSR (stay resident)", 0x28: "DOS idle",
    0x2F: "DOS multiplex", 0x31: "DPMI", 0x33: "MOUSE driver",
    0x60: "user/packet driver range", 0x61: "PACKET DRIVER (network)",
    0x62: "user", 0x67: "EMS memory manager", 0x7A: "IPX (network)",
}

# Ports the game is documented to need (Sound Blaster + VGA).
def port_meaning(p):
    if p is None:
        return "dynamic (DX)"
    if 0x3C0 <= p <= 0x3DF:
        return "VGA/CRTC"
    if 0x220 <= p <= 0x22F or 0x240 <= p <= 0x24F:
        return "Sound Blaster"
    if 0x388 <= p <= 0x38B:
        return "AdLib/OPL FM"
    if 0x00 <= p <= 0x0F or 0x80 <= p <= 0x8F or 0xC0 <= p <= 0xDF:
        return "DMA controller"
    if p in (0x20, 0x21, 0xA0, 0xA1):
        return "PIC (interrupt controller)"
    if p in (0x40, 0x41, 0x42, 0x43):
        return "PIT (timer)"
    if p in (0x60, 0x61, 0x64):
        return "keyboard controller"
    if p == 0x201:
        return "joystick"
    return "?"


data = open(EXE, "rb").read()
(cblp, cp, crlc, cparhdr, mn, mx, ss, sp, csum, ip, cs,
 lfarlc, ovno) = struct.unpack_from("<13H", data, 2)
img = data[cparhdr * 16:]

# Borland's startup loads DGROUP with `mov di, <imm>`; the value at entry+0
# (`mov dx, 0x1895`) is that segment. Code lives below it, data above.
dgroup = struct.unpack_from("<H", img, cs * 16 + ip + 1)[0]
code_end = dgroup * 16
print(f"=== layout ===")
print(f"  image        : {len(img)} bytes")
print(f"  DGROUP seg   : {dgroup:#06x}  -> code {0:#x}..{code_end:#x} "
      f"({code_end} bytes), data {code_end:#x}..{len(img):#x} "
      f"({len(img) - code_end} bytes)")

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
md.detail = True

# ---- linear sweep, recording instruction boundaries per start offset -------
BOUNDARY_PROBES = 12


def sweep(start, end):
    """Yield (offset, insn) for a linear disassembly."""
    for insn in md.disasm(img[start:end], start):
        yield insn.address, insn


def confirmed(addr, probes=BOUNDARY_PROBES):
    """True if independent sweeps from earlier offsets land exactly on addr."""
    hits = 0
    for back in range(1, probes + 1):
        s = addr - back * 5
        if s < 0:
            continue
        for a, _ in sweep(s, addr + 2):
            if a == addr:
                hits += 1
                break
            if a > addr:
                break
    return hits


ints = defaultdict(list)     # int_no -> [offsets]
io_in, io_out = Counter(), Counter()
ah_before = {}

last_ah = None
last_ax = None
for addr, insn in sweep(0, code_end):
    m, ops = insn.mnemonic, insn.op_str
    if m == "mov" and ops.startswith("ah, 0x"):
        last_ah = int(ops.split("0x")[1], 16)
    elif m == "mov" and ops.startswith("ax, 0x"):
        last_ax = int(ops.split("0x")[1], 16)
        last_ah = (last_ax >> 8) & 0xFF
    elif m == "int":
        n = int(ops, 16)
        ints[n].append((addr, last_ah))
    elif m == "in":
        p = ops.split(", ")[-1]
        io_in[int(p, 16) if p.startswith("0x") else None] += 1
    elif m == "out":
        p = ops.split(", ")[0]
        io_out[int(p, 16) if p.startswith("0x") else None] += 1

print(f"\n=== interrupt census (linear sweep, then convergence-confirmed) ===")
print(f"{'INT':>5} {'raw':>5} {'confirmed':>9}   meaning")
real_ints = {}
for n in sorted(ints):
    sites = ints[n]
    conf = [(a, ah) for a, ah in sites if confirmed(a) >= BOUNDARY_PROBES // 2]
    if conf:
        real_ints[n] = conf
    print(f"  {n:02x}h {len(sites):>5} {len(conf):>9}   "
          f"{INT_MEANING.get(n, 'unassigned/likely data')}")

print(f"\n=== INT 21h (DOS) functions actually invoked ===")
fns = Counter(ah for a, ah in real_ints.get(0x21, []))
for ah, cnt in sorted(fns.items(), key=lambda kv: (kv[0] is None, kv[0])):
    if ah is None:
        print(f"  AH=?    x{cnt:<4} (AH set indirectly - resolved dynamically)")
    else:
        print(f"  AH={ah:02x}h x{cnt:<4} {DOS_FN.get(ah, '?')}")

print(f"\n=== port I/O ===")
for label, ctr in (("IN ", io_in), ("OUT", io_out)):
    for p, cnt in sorted(ctr.items(), key=lambda kv: (kv[0] is None, kv[0])):
        tag = f"{p:#05x}" if p is not None else "  DX "
        print(f"  {label} {tag} x{cnt:<4} {port_meaning(p)}")

# ---- negative checks -------------------------------------------------------
print(f"\n=== negative checks ===")
DANGER = {0x13: "raw disk I/O", 0x27: "TSR / stay resident",
          0x61: "packet driver (networking)", 0x7A: "IPX networking",
          0x31: "DPMI", 0x2F: "DOS multiplex"}
for n, what in sorted(DANGER.items()):
    conf = real_ints.get(n, [])
    print(f"  INT {n:02x}h ({what}): "
          f"{'ABSENT' if not conf else f'PRESENT at {len(conf)} sites!'}")

exec_sites = [ah for a, ah in real_ints.get(0x21, []) if ah == 0x4B]
print(f"  INT 21h AH=4Bh (EXEC another program): "
      f"{'ABSENT' if not exec_sites else 'PRESENT!'}")

# ---- filename / path strings ----------------------------------------------
print(f"\n=== filename-like strings in the data segment ===")
import re
seg = img[code_end:]
pat = re.compile(rb"[ -~]{3,}")
names = []
for m_ in pat.finditer(seg):
    s = m_.group()
    if re.search(rb"\.(EGG|DAT|SG|EXE|COM|BAT|CFG|SAV|dat|egg|sg)\b", s) or b"\\" in s:
        names.append((code_end + m_.start(), s))
for off, s in names:
    print(f"  {off:#08x}  {s.decode('latin1')!r}")
