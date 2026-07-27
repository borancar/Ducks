#!/usr/bin/env python3
"""Make a CPU fault say where it came from, not just where it died.

"Invalid instruction at 19a5:210a" is the address of the wreck. 0x19a5 is the
DGROUP segment, so that is data being executed - which means the interesting
question is who jumped there, and the stack still knows: the top words hold return
addresses, and the BP chain leads back through the callers.

Same walk find_int86.py used to locate int86, but wired into the fault path and
resolved through the function map, so a single reproduction names the caller.

    venv/bin/python edit_crash_report.py
"""
import sys

SRC = "native.py"

OLD = '''            except UcError as e:
                print(f"  [cpu] {e} at {m._reg(UC_X86_REG_CS):04x}:"
                      f"{m._reg(UC_X86_REG_IP):04x}")
                running = False
                break
'''

NEW = '''            except UcError as e:
                print(f"  [cpu] {e} at {m._reg(UC_X86_REG_CS):04x}:"
                      f"{m._reg(UC_X86_REG_IP):04x}")
                m.crash_report(img)
                running = False
                break
'''

METHOD_ANCHOR = "    def bench_report(self):"
METHOD_ANCHOR_ALT = "    def fp_report(self, img):"

METHOD = '''    def crash_report(self, img=None):
        """Where the fault came from: the stack and the frame chain, resolved.

        A fault address alone rarely identifies a bug. When the address is not even
        in the code - executing DGROUP, say - it identifies nothing at all, and the
        only record of how control got there is the stack.

        Every candidate is printed with what it resolves to, rather than picking
        one: a stack word that happens to look like an address is not a caller, and
        deciding which are real is a judgement to make while reading, not one to
        bury in here.
        """
        cs, ip = self._reg(UC_X86_REG_CS), self._reg(UC_X86_REG_IP)
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
        bp = self._reg(UC_X86_REG_BP)
        dgroup_seg = (self.dgroup_base) // 16
        lin = cs * 16 + ip

        def where(addr):
            """Describe a linear address: which image region, which function."""
            off = addr - self.image_base
            if not (0 <= off < 0x20000):
                return f"{addr:#07x} outside the image"
            if off >= DGROUP_IMAGE_OFF:
                return (f"image {off:#07x} = DGROUP+{off - DGROUP_IMAGE_OFF:#06x}"
                        f" (DATA, not code)")
            fn = find_function_start(img, off) if img is not None else None
            if fn is None:
                return f"image {off:#07x} (in no function)"
            return f"image {off:#07x} in {fn:#07x}"

        print(f"  [crash] CS:IP {cs:04x}:{ip:04x} -> {where(lin)}")
        if cs == dgroup_seg:
            print(f"  [crash] CS is the DGROUP segment, so this is data being "
                  f"executed - something transferred control to a data address")
        try:
            print(f"  [crash] bytes there: "
                  f"{bytes(self.uc.mem_read(lin, 16)).hex(' ')}")
        except Exception:
            print("  [crash] bytes there: unreadable")

        try:
            words = struct.unpack("<12H", self.uc.mem_read(ss * 16 + sp, 24))
        except Exception:
            words = ()
        print(f"  [crash] SS:SP {ss:04x}:{sp:04x} BP {bp:04x}")
        for i in range(0, max(0, len(words) - 1)):
            near, far_ = cs * 16 + words[i], words[i + 1] * 16 + words[i]
            print(f"  [crash]   [sp+{2 * i:#04x}] {words[i]:04x}  "
                  f"as near return {where(near)}")
            if i + 1 < len(words):
                print(f"  [crash]            {words[i + 1]:04x}:{words[i]:04x}"
                      f"  as far return {where(far_)}")

        seen, frame = set(), bp
        for depth in range(8):
            if not frame or frame in seen:
                break
            seen.add(frame)
            try:
                nbp, roff, rseg = struct.unpack(
                    "<3H", self.uc.mem_read(ss * 16 + frame, 6))
            except Exception:
                break
            print(f"  [crash] frame {depth}: BP {frame:04x} -> caller "
                  f"{where(rseg * 16 + roff)}")
            frame = nbp

'''


def main():
    src = open(SRC).read()
    if src.count(OLD) != 1:
        print("crash-handler anchor missing, nothing written")
        return 1
    anchor = METHOD_ANCHOR if METHOD_ANCHOR in src else METHOD_ANCHOR_ALT
    if src.count(anchor) != 1:
        print("method anchor missing, nothing written")
        return 1
    src = src.replace(OLD, NEW, 1).replace(anchor, METHOD + anchor, 1)
    open(SRC, "w").write(src)
    print("native.py updated: crash_report added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
