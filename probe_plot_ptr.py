#!/usr/bin/env python3
"""Check that [0x53e] resolves to a known plotter, without needing to play.

The scroll caller's plane loop takes its row stride from the far pointer at
DGROUP:0x53e. Getting that resolution wrong is silent - the loop just skips its
pixel run - so it is worth proving before spending a play session on --verify
again. The game sets the pointer when it sets the video mode, which happens on the
way to the title screen, so a short unattended run should be enough.

It is not: the slot reads 0000:0000 for the whole run, so the game sets it later
than the title screen - when a level starts, which needs input. Kept because that
is worth knowing (a null there is a real state, not a bug in the read), and
because it is the cheapest way to re-check the resolution if the pointer ever
moves.

    venv/bin/python probe_plot_ptr.py
"""
import struct
import sys
import time

import pygame
from unicorn.x86_const import *

from native import Native, plot_pixel_stride, PLOT_PIXEL_STRIDE


def main():
    pygame.init()
    pygame.display.set_mode((320, 200))
    m = Native("Ducks.unpacked.exe", blaster=True, native_sound=True,
               native_mouse=True, native_keyboard=True, native_file=True,
               native_xms=True, native_setup=True, max_insns=1 << 62)
    m.install_int_stubs()
    m.install_native_xms()
    m.install_native_fp()

    addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
    deadline = time.time() + 20
    seen = None
    while time.time() < deadline:
        try:
            m.uc.emu_start(addr, 0, count=400_000)
        except Exception as e:
            print(f"  [cpu] {e}")
            break
        addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
        pygame.event.pump()
        off, seg = struct.unpack("<HH", m.read(m.dgroup_base + 0x53E, 4))
        if (off, seg) != seen:
            seen = (off, seg)
            target = seg * 16 + off - m.image_base
            stride = plot_pixel_stride(m)
            print(f"  [0x53e] = {seg:04x}:{off:04x} -> image {target:#07x}"
                  f"  stride {stride}")
    pygame.quit()
    print("known plotters:",
          {hex(k): v for k, v in PLOT_PIXEL_STRIDE.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
