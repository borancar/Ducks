#!/usr/bin/env python3
"""Catch the bad iret at the instruction, and say what unbalanced the stack.

The readme crash is an iret popping 19a5:1d82 as CS:IP - segment values, not a
return frame - which lands exactly where the runaway starts. There are only two
irets in the image: 0x00eb4 in the INT 23h handler and 0x15916 in the Sound Blaster
IRQ handler. Both are the pop-nine-registers-and-iret shape, so a frame that is not
a frame means the stack was already wrong when the iret was reached.

This records SP when a handler is entered and checks it at the iret. The two must
match: nine pushes, nine pops. A mismatch gives the handler, the direction and the
size of the imbalance - and the natives called in between, which is where a wrong
call convention would show up.

    venv/bin/python edit_iret_guard.py
"""
import sys

SRC = "native.py"

STATE_OLD = "        self.wild_reported = False\n"
STATE_NEW = ("        self.wild_reported = False\n"
             "        self.iret_sites = {}\n"
             "        self.isr_entry = {}\n"
             "        self.native_ring = deque(maxlen=32)\n"
             "        self.iret_reported = 0\n")

INSTALL = '''    def install_iret_guard(self, img):
        """Hook every iret in the image, and the handler entry that reaches it.

        The handler entry is the byte after the preceding return: Borland emits the
        interrupt wrapper immediately after the routine that installs it, so the
        wrapper is not a function of its own and the function map has no entry for
        it.

        Cheap: two addresses in the whole image, so the hooks fire only when an
        interrupt handler actually runs.
        """
        md = _disasm16()
        if md is None:
            return
        norm = _fp_normalised(img)
        _, spans = _function_map(img)
        for start, end in spans:
            prev_ret = None
            for i in md.disasm(norm[start:end], start):
                if i.mnemonic == "iret":
                    entry = prev_ret if prev_ret is not None else start
                    self.iret_sites[self.image_base + i.address] = entry
                    lin_entry = self.image_base + entry
                    self.uc.hook_add(UC_HOOK_CODE, self._on_isr_entry, None,
                                     lin_entry, lin_entry)
                    self.uc.hook_add(UC_HOOK_CODE, self._on_iret, None,
                                     self.image_base + i.address,
                                     self.image_base + i.address)
                    print(f"  [iret] guarding iret {i.address:#07x}, handler "
                          f"entry {entry:#07x}")
                if i.mnemonic in ("ret", "retf"):
                    prev_ret = i.address + i.size
        return

    def _on_isr_entry(self, uc, address, size, user):
        off = address - self.image_base
        self.isr_entry[off] = (self._reg(UC_X86_REG_SP), len(self.native_ring))

    def _on_iret(self, uc, address, size, user):
        """Check the stack at an iret against what the handler entered with."""
        entry = self.iret_sites.get(address)
        if entry is None or self.iret_reported >= 3:
            return
        rec = self.isr_entry.get(entry)
        sp, ss = self._reg(UC_X86_REG_SP), self._reg(UC_X86_REG_SS)
        ip, cs, fl = struct.unpack("<3H", uc.mem_read(ss * 16 + sp, 6))
        target = cs * 16 + ip
        code_lo, code_hi = self.image_base, self.image_base + DGROUP_IMAGE_OFF
        sane = code_lo <= target < code_hi
        if rec is not None and sp == rec[0] and sane:
            return                      # balanced and returning into code
        self.iret_reported += 1
        print(f"\\n  [iret] BAD iret at {address - self.image_base:#07x} "
              f"(handler {entry:#07x})")
        if rec is None:
            print("  [iret] the handler entry was never seen, so control reached "
                  "this iret without going through the top of the handler")
        else:
            print(f"  [iret] SP {sp:04x}, but the handler was entered with "
                  f"{rec[0]:04x} - off by {sp - rec[0]:+d} bytes")
        print(f"  [iret] frame it will pop: {cs:04x}:{ip:04x} flags {fl:04x}"
              f"  -> {'code' if sane else 'NOT CODE'}")
        since = list(self.native_ring)[rec[1]:] if rec else list(self.native_ring)
        print(f"  [iret] natives called since entry ({len(since)}): "
              + (", ".join(since) if since else "none"))

'''

# A ring of native calls, so the report can say what ran inside the handler.
RING_OLD = '''        name, handler, kind = entry
        self.native_calls[name] += 1
'''
RING_NEW = '''        name, handler, kind = entry
        self.native_calls[name] += 1
        # Kept for the iret guard: which natives ran inside an interrupt handler
        # is exactly what a stack imbalance would be traced through.
        self.native_ring.append(name)
'''

MAIN_OLD = "    m.install_wild_jump_trap()\n"
MAIN_NEW = ("    m.install_wild_jump_trap()\n"
            "    m.install_iret_guard(img)\n")

IMPORT_OLD = "from collections import Counter, defaultdict\n"
IMPORT_NEW = "from collections import Counter, defaultdict, deque\n"


def main():
    src = open(SRC).read()
    edits = [(IMPORT_OLD, IMPORT_NEW), (STATE_OLD, STATE_NEW),
             (RING_OLD, RING_NEW), (MAIN_OLD, MAIN_NEW),
             ("    def install_wild_jump_trap(self):",
              INSTALL + "    def install_wild_jump_trap(self):")]
    for old, new in edits:
        if src.count(old) != 1:
            print(f"anchor found {src.count(old)} times, nothing written: "
                  f"{old[:48]!r}")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: iret guard installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
