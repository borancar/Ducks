#!/usr/bin/env python3
"""Catch the instant control leaves the code, not 900 bytes later.

The readme crash reports CS:IP in DGROUP with SS:SP 0423:51f5, BP 0003 and both TF
and DF set - none of which is the state at the moment of the wild transfer. It is
the state after ~450 bytes of BSS have been executed as instructions, which trashes
every register including SS. So the stack walk at the fault is worthless, and the
first INT 01h is not the arrival either: TF may have been set by the garbage rather
than at the jump.

A code hook over the data region fires on the *first* instruction executed there,
while the registers still mean something and the popped return address is still
sitting just below SP.

    venv/bin/python edit_wild_jump.py
"""
import sys

SRC = "native.py"

STATE_OLD = "        self.plot_pixel_warned = False\n"
STATE_NEW = ("        self.plot_pixel_warned = False\n"
             "        self.wild_reported = False\n"
             "        self.report_img = None\n")

INSTALL = '''    def install_wild_jump_trap(self):
        """Report the first instruction executed outside the code, then shut up.

        Costs nothing while nothing goes wrong: the hook covers only the data
        region, where no instruction should ever execute. It fires once, so a
        runaway that crawls a thousand bytes of BSS produces one report rather
        than a thousand.

        One legitimate exception exists and is skipped: Borland's int86 builds a
        two-instruction stub on the stack and calls it, which is executing outside
        the code by design. It only happens with --no-native-setup, since the
        native serves int86 without building anything.
        """
        lo = self.image_base + DGROUP_IMAGE_OFF
        hi = self.image_base + 0x20000
        self.uc.hook_add(UC_HOOK_CODE, self._on_wild_jump, None, lo, hi)

    def _on_wild_jump(self, uc, address, size, user):
        if self.wild_reported:
            return
        ss = self._reg(UC_X86_REG_SS)
        if ss * 16 <= address < ss * 16 + 0x10000:
            return                      # a stub on the stack: int86 does this
        self.wild_reported = True
        off = address - self.image_base
        print(f"  [wild] control just left the code: executing {address:#07x} = "
              f"DGROUP+{off - DGROUP_IMAGE_OFF:#06x}, which is data")
        print("  [wild] state below is from the moment of arrival, so the words "
              "just under SP are whatever a bad return popped")
        self.crash_report()

'''

# Show a few words below SP as well: a return that popped garbage has already
# advanced SP past it, but the values are still in memory.
STACK_OLD = '''        try:
            words = struct.unpack("<12H", self.uc.mem_read(ss * 16 + sp, 24))
        except Exception:
            words = ()
'''
STACK_NEW = '''        try:
            below = struct.unpack("<4H", self.uc.mem_read(ss * 16 + sp - 8, 8))
            for i, w in enumerate(below):
                print(f"  [crash]   [sp-{8 - 2 * i:#04x}] {w:04x}  "
                      f"already popped: {where(self._reg(UC_X86_REG_CS) * 16 + w)}")
        except Exception:
            pass
        try:
            words = struct.unpack("<12H", self.uc.mem_read(ss * 16 + sp, 24))
        except Exception:
            words = ()
'''

IMG_OLD = "    def crash_report(self, img=None):\n"
IMG_NEW = "    def crash_report(self, img=None):\n"

IMG_BODY_OLD = '''        cs, ip = self._reg(UC_X86_REG_CS), self._reg(UC_X86_REG_IP)
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
'''
IMG_BODY_NEW = '''        img = self.report_img if img is None else img
        cs, ip = self._reg(UC_X86_REG_CS), self._reg(UC_X86_REG_IP)
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
'''

MAIN_OLD = '''    _d = open(args.unpacked, "rb").read()
    img = _d[struct.unpack_from("<13H", _d, 2)[3] * 16:]
'''
MAIN_NEW = '''    _d = open(args.unpacked, "rb").read()
    img = _d[struct.unpack_from("<13H", _d, 2)[3] * 16:]
    m.report_img = img
    m.install_wild_jump_trap()
'''


def main():
    src = open(SRC).read()
    edits = [(STATE_OLD, STATE_NEW), (STACK_OLD, STACK_NEW),
             (IMG_BODY_OLD, IMG_BODY_NEW), (MAIN_OLD, MAIN_NEW),
             ("    def crash_report(self, img=None):", INSTALL +
              "    def crash_report(self, img=None):")]
    for old, new in edits:
        if src.count(old) != 1:
            print(f"anchor found {src.count(old)} times, nothing written: "
                  f"{old[:48]!r}")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: wild-jump trap installed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
