"""Walk the call graph under run_level() and report what does I/O.

Per function: disassemble its extent (with the 8087 emulator's INT 34h..3Bh put
back, or capstone desyncs), and record port I/O, software interrupts and far
calls out of the segment. The point is to find out whether a piece can be run
under Unicorn without a shim answering anything.
"""
import sys
from bisect import bisect_right
sys.path.insert(0, "/home/boran/git/Ducks")
from capstone import Cs, CS_ARCH_X86, CS_MODE_16
import read_fn, symbols
from native import _entry_table

SEG = read_fn.SEG_BASE
DGROUP = 0x18950
img = read_fn.image()
offs, entries = _entry_table(img)
md = Cs(CS_ARCH_X86, CS_MODE_16)


def extent(a):
    k = bisect_right(offs, a)
    nxt = next((o for o in offs[k:] if o in entries), None)
    return a, (nxt or DGROUP)


def scan(a):
    """(calls, ports, ints, farcalls) for the function starting at a."""
    s, e = extent(a)
    code = bytearray(img[s:e])
    i = 0
    while i < len(code) - 1:
        if code[i] == 0xCD and 0x34 <= code[i + 1] <= 0x3B:
            code[i] = 0x90
            code[i + 1] = 0xD8 + (code[i + 1] - 0x34)
        i += 1
    calls, ports, ints, fars = set(), [], [], []
    for ins in md.disasm(bytes(code), s - SEG):
        at = SEG + (ins.address & 0xFFFF)
        m, o = ins.mnemonic, ins.op_str
        if m == 'call' and o.startswith('0x'):
            calls.add(SEG + (int(o, 16) & 0xFFFF))
        elif m == 'lcall':
            fars.append((at, o))
        elif m in ('in', 'out'):
            ports.append((at, m, o))
        elif m == 'int':
            ints.append((at, o))
    return calls, ports, ints, fars


seen, todo = set(), [0x0d7ee]
rows = []
while todo:
    a = todo.pop()
    if a in seen or not (SEG <= a < DGROUP) or a not in entries:
        continue
    seen.add(a)
    calls, ports, ints, fars = scan(a)
    rows.append((a, ports, ints, fars))
    todo.extend(calls)

rows.sort()
print("functions reachable from run_level(): %d" % len(rows))
nport = nint = 0
for a, ports, ints, fars in rows:
    if ports or ints:
        print("  %#07x %-22s" % (a, symbols.name(a) or ''))
        for p in ports:
            print("      %#07x  %s %s" % p)
            nport += 1
        for p in ints:
            print("      %#07x  int %s" % p)
            nint += 1
print("port accesses: %d   software interrupts: %d" % (nport, nint))

fc = {}
for a, ports, ints, fars in rows:
    for at, o in fars:
        fc.setdefault(o, []).append(at)
print("\ndistinct far-call targets: %d" % len(fc))
for o in sorted(fc, key=lambda k: -len(fc[k])):
    print("  %-16s x%-3d  first at %#07x" % (o, len(fc[o]), fc[o][0]))
