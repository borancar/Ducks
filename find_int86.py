#!/usr/bin/env python3
"""Locate Borland's int86 by walking back from the interrupt it builds.

int86 exists because the interrupt number is a variable, and x86 has no
"INT reg". So the runtime keeps a tiny code buffer, patches the number into its
CD xx at call time, and calls it. That is why the last two interrupts in a
session execute from BSS rather than from the image: there is no call site to
find statically, and nothing to disassemble at the address.

The stack still knows. At the moment the interrupt fires, the top of stack holds
the return address back into int86's own body, and the frame chain above it leads
to whoever called int86. This prints both, resolved to function entries, for
every interrupt raised from outside the loaded image.

    venv/bin/python find_int86.py
"""
import struct
import sys

import pygame
from unicorn.x86_const import *

import native
from native import Native, find_function_start


class Finder(Native):
    def __init__(self, *a, **kw):
        self.reported = 0
        super().__init__(*a, **kw)

    def _name(self, lin):
        """Describe a linear address as an image offset and enclosing function."""
        off = lin - self.image_base
        if not (0 <= off < len(self.img)):
            return f"{lin:#07x} (outside the image)"
        fn = find_function_start(self.img, off)
        return f"{off:#07x}" + (f" in {fn:#07x}" if fn is not None else " (no prologue)")

    def _on_intr(self, uc, intno, user):
        cs, ip = self._reg(UC_X86_REG_CS), self._reg(UC_X86_REG_IP)
        site = cs * 16 + ((ip - 2) & 0xFFFF)
        if not (0 <= site - self.image_base < len(self.img)) and self.reported < 6:
            self.reported += 1
            ss, sp, bp = (self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP),
                          self._reg(UC_X86_REG_BP))
            ax = self._reg(UC_X86_REG_AX)
            print(f"\n=== INT {intno:02x}h AH={ax >> 8:02x}h from {cs:04x}:{ip - 2:04x} "
                  f"(linear {site:#07x}, outside the image) ===")
            words = struct.unpack("<8H", uc.mem_read(ss * 16 + sp, 16))
            print(f"  stack at {ss:04x}:{sp:04x}: " +
                  " ".join(f"{w:04x}" for w in words))
            # The buffer is reached by a call, so [SP] is the return address into
            # int86. Try both conventions: near leaves one word, far leaves two.
            print(f"  near return -> {self._name(cs * 16 + words[0])}")
            print(f"  far  return -> {self._name(words[1] * 16 + words[0])}")
            frames, seen = [], set()
            while bp and bp not in seen and len(frames) < 6:
                seen.add(bp)
                try:
                    nbp, roff, rseg = struct.unpack(
                        "<3H", uc.mem_read(ss * 16 + bp, 6))
                except Exception:
                    break
                frames.append(rseg * 16 + roff)
                bp = nbp
            for i, f in enumerate(frames):
                print(f"  caller frame {i}: {self._name(f)}")
        return super()._on_intr(uc, intno, user)


def main():
    d = open("Ducks.unpacked.exe", "rb").read()
    pygame.init()
    m = Finder("Ducks.unpacked.exe", blaster=True, native_sound=True,
               native_mouse=True, native_keyboard=True, native_file=True,
               native_xms=True, native_setup=True, max_insns=1 << 62)
    m.img = d[struct.unpack_from("<13H", d, 2)[3] * 16:]
    m.install_int_stubs()
    m.install_native_xms()
    m.install_native_fp()
    pygame.display.set_mode((320, 200))
    addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
    for _ in range(400):
        try:
            m.uc.emu_start(addr, 0, count=400_000)
        except Exception as e:
            print(f"  [cpu] {e}")
            break
        addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
        pygame.event.pump()
        if m.reported >= 2:
            print("\nboth int86 interrupts seen; stopping")
            break
    pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
