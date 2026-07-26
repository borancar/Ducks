#!/usr/bin/env python3
"""
Run Ducks in the emulator with a real SDL window: VGA output, keyboard, mouse.

Extends the DOS shim in trace_dos.py with:
  * a VGA model - DAC palette (ports 0x3c8/0x3c9), mode tracking, and the
    0xa0000 framebuffer blitted to an SDL surface
  * wall-clock timing - the PIT counter and the 0x3da vertical-retrace bit are
    derived from real elapsed time, so the game paces itself correctly instead
    of spinning
  * live input - host keyboard fed through the BIOS INT 16h buffer, host mouse
    through INT 33h

The host filesystem stays READ-ONLY, exactly as in trace_dos.py: the game's
writes to settings.dat and save files are intercepted in memory. Nothing in the
game directory is modified.

Usage:
    python play.py                        # interactive window
    python play.py --scale 3
    python play.py --shots 6 --shot-every 2.0   # save PNGs and exit
    python play.py --blaster              # advertise a Sound Blaster
"""
import argparse
import os
import struct
import sys
import time
from collections import Counter, deque

os.environ.setdefault("SDL_VIDEODRIVER", "x11")

import pygame
from unicorn import *
from unicorn.x86_const import *
from trace_dos import DosMachine

PIT_HZ = 1193182.0
VGA_A000 = 0xA0000

# pygame key -> (BIOS scancode, ASCII)
KEYMAP = {
    pygame.K_ESCAPE: (0x01, 0x1B), pygame.K_RETURN: (0x1C, 0x0D),
    pygame.K_SPACE: (0x39, 0x20), pygame.K_BACKSPACE: (0x0E, 0x08),
    pygame.K_TAB: (0x0F, 0x09),
    pygame.K_UP: (0x48, 0x00), pygame.K_DOWN: (0x50, 0x00),
    pygame.K_LEFT: (0x4B, 0x00), pygame.K_RIGHT: (0x4D, 0x00),
    pygame.K_HOME: (0x47, 0x00), pygame.K_END: (0x4F, 0x00),
    pygame.K_PAGEUP: (0x49, 0x00), pygame.K_PAGEDOWN: (0x51, 0x00),
    pygame.K_LEFTBRACKET: (0x1A, 0x5B), pygame.K_RIGHTBRACKET: (0x1B, 0x5D),
    pygame.K_COMMA: (0x33, 0x2C), pygame.K_PERIOD: (0x34, 0x2E),
    pygame.K_MINUS: (0x0C, 0x2D), pygame.K_EQUALS: (0x0D, 0x3D),
    pygame.K_SEMICOLON: (0x27, 0x3B), pygame.K_SLASH: (0x35, 0x2F),
}
for i, k in enumerate("qwertyuiop"):
    KEYMAP[getattr(pygame, f"K_{k}")] = (0x10 + i, ord(k))
for i, k in enumerate("asdfghjkl"):
    KEYMAP[getattr(pygame, f"K_{k}")] = (0x1E + i, ord(k))
for i, k in enumerate("zxcvbnm"):
    KEYMAP[getattr(pygame, f"K_{k}")] = (0x2C + i, ord(k))
for i in range(1, 10):
    KEYMAP[getattr(pygame, f"K_{i}")] = (0x02 + i - 1, ord(str(i)))
KEYMAP[pygame.K_0] = (0x0B, ord("0"))
for i in range(1, 11):
    KEYMAP[getattr(pygame, f"K_F{i}")] = (0x3A + i - 1, 0x00)

MODE_GEOM = {0x13: (320, 200), 0x00: (320, 200), 0x01: (320, 200),
             0x04: (320, 200), 0x05: (320, 200), 0x0D: (320, 200),
             0x0E: (640, 200), 0x10: (640, 350), 0x12: (640, 480)}


class VgaDos(DosMachine):
    def __init__(self, exe, blaster=False, **kw):
        self.palette = [(0, 0, 0)] * 256
        self.dac_index = 0
        self.dac_phase = 0
        self.dac_latch = []
        self.seq_index = 0
        self.chain4 = True
        self.map_mask = 0x0F
        self.active_planes = (0, 1, 2, 3)
        self.planes = [bytearray(0x10000) for _ in range(4)]
        self.crtc = {}
        self.crtc_index = 0
        self.start_addr = 0
        self.crtc_offset = 0
        self.start_mult = 4
        self._warned_range = False
        self.mode = 0x03
        self.width, self.height = 320, 200
        self.key_buf = deque()
        self.last_scancode = 0
        self.mouse_pos = (160, 100)
        self.mouse_btn = 0
        self.mouse_rel = [0, 0]
        self.pit_latch_toggle = {}
        self.pit_initial = 0xFFFF
        self.t0 = time.perf_counter()
        self.palette_writes = 0
        self.int10_fn = Counter()
        self.text_mode = True             # DOS hands us mode 03h
        self.vidwrites = Counter()
        self.vidrange = {}
        super().__init__(exe, blaster=blaster, verbose=False, **kw)
        # Watch the video apertures so we can tell where the game actually
        # draws: 0xa0000 (graphics) vs 0xb8000 (colour text) vs 0xb0000 (mono).
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._on_vidwrite,
                         None, 0xA0000, 0xBFFFF)
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._on_plane_write,
                         None, 0xA0000, 0xAFFFF)

    def _on_vidwrite(self, uc, access, address, size, value, user):
        if address >= 0xB8000:
            k = "b800(text)"
        elif address >= 0xB0000:
            k = "b000(mono)"
        else:
            k = "a000(gfx)"
        self.vidwrites[k] += size
        lo, hi = self.vidrange.get(k, (1 << 30, 0))
        self.vidrange[k] = (min(lo, address), max(hi, address + size))

    # ------------------------------------------------------------ timing
    def _elapsed(self):
        return time.perf_counter() - self.t0

    # ------------------------------------------------------------- ports
    def _on_out(self, uc, port, size, value, user):
        self.port_out[port] += 1
        v = value & 0xFF
        if port == 0x3C8:                     # DAC write index
            self.dac_index = v
            self.dac_phase = 0
            self.dac_latch = []
        elif port == 0x3C9:                   # DAC data: R, G, B (6-bit)
            self.dac_latch.append(v & 0x3F)
            if len(self.dac_latch) == 3:
                r, g, b = (c * 255 // 63 for c in self.dac_latch)
                self.palette[self.dac_index & 0xFF] = (r, g, b)
                self.dac_index = (self.dac_index + 1) & 0xFF
                self.dac_latch = []
                self.palette_writes += 1
        elif port == 0x3C4:
            self.seq_index = v
            if size == 2:                     # OUT dx,ax -> index+data in one
                self._seq_write(v, (value >> 8) & 0xFF)
        elif port == 0x3C5:
            self._seq_write(self.seq_index, v)
        elif port == 0x3D4:
            self.crtc_index = v
            if size == 2:
                self._crtc_write(v, (value >> 8) & 0xFF)
        elif port == 0x3D5:
            self._crtc_write(self.crtc_index, v)
        elif port == 0x43:
            self.pit_latch_toggle[(v >> 6) & 3] = 0
        elif port == 0x40:
            self.pit_initial = v | (self.pit_initial & 0xFF00)

    def _seq_write(self, index, v):
        if index == 0x02:                     # map mask: which planes to write
            self.map_mask = v & 0x0F
            self.active_planes = tuple(p for p in range(4) if v & (1 << p))
        elif index == 0x04:                   # memory mode
            new_chain4 = bool(v & 0x08)
            if new_chain4 != self.chain4:
                print(f"  [vga] chain-4 "
                      f"{'ON (linear mode 13h)' if new_chain4 else 'OFF (Mode X planar)'}")
            self.chain4 = new_chain4

    def _crtc_write(self, index, v):
        self.crtc[index] = v
        if index in (0x0C, 0x0D):             # display start address
            self.start_addr = (self.crtc.get(0x0C, 0) << 8) | \
                self.crtc.get(0x0D, 0)
        elif index == 0x13:                   # logical line width
            self.crtc_offset = v
        elif index in (0x14, 0x17):
            self._update_addr_mode()

    def _update_addr_mode(self):
        """Determine what unit the CRTC start address is counted in.

        The start address is not necessarily a byte offset. Underline Location
        (0x14) bit 6 selects doubleword addressing; failing that, Mode Control
        (0x17) bit 6 picks byte (1) or word (0). Ducks sets 0x17=0xe3, 0x14=0x00
        -> byte addressing, so its page-flip value 0x7d00 means offset 32000,
        not 128000. Assuming doubleword puts page 1 past the end of the plane,
        which renders black and looks like flicker as the game flips pages.
        """
        old = self.start_mult
        if self.crtc.get(0x14, 0) & 0x40:
            self.start_mult = 4
        elif self.crtc.get(0x17, 0) & 0x40:
            self.start_mult = 1
        else:
            self.start_mult = 2
        if self.start_mult != old:
            unit = {1: "bytes", 2: "words", 4: "doublewords"}[self.start_mult]
            print(f"  [vga] CRTC start address counted in {unit} "
                  f"(0x14={self.crtc.get(0x14, 0):#04x} "
                  f"0x17={self.crtc.get(0x17, 0):#04x})")

    def _on_in(self, uc, port, size, user):
        self.port_in[port] += 1
        n = self.port_in[port]
        el = self._elapsed()
        if port == 0x3DA:
            # Bit 3 = vertical retrace, at the ~70 Hz frame rate (wall clock, so
            # the game paces its frames correctly).
            # Bit 0 = display enable, which runs at the ~31.5 kHz HORIZONTAL
            # rate. The snow-avoidance blit at 0x1ddf waits for a full 0->1
            # transition of bit 0 for every single word it copies, so this bit
            # must flip far faster than the emulator can be clocked from wall
            # time; toggle it per read instead.
            vsync = 0x08 if (el * 70.0) % 1.0 > 0.92 else 0x00
            return vsync | (0x01 if (n & 1) else 0x00)
        if port in (0x40, 0x41, 0x42):
            ch = port - 0x40
            counter = int(PIT_HZ * el) & 0xFFFF
            counter = (0x10000 - counter) & 0xFFFF
            t = self.pit_latch_toggle.get(ch, 0)
            self.pit_latch_toggle[ch] = 1 - t
            return counter & 0xFF if t == 0 else (counter >> 8) & 0xFF
        if port == 0x60:
            return self.last_scancode
        if port == 0x61:
            return 0x20
        if port == 0x201:
            return 0xFF
        if port == 0x22A:
            return 0xAA
        if port == 0x22C:
            return 0x00
        if port == 0x22E:
            return 0x80
        return 0x00

    # ------------------------------------------------------------- input
    def _bios_kbd(self):
        ah = self._reg(UC_X86_REG_AX) >> 8
        f = self.uc.reg_read(UC_X86_REG_EFLAGS)
        if ah in (0x01, 0x11):
            if self.key_buf:
                sc, asc = self.key_buf[0]
                self._set(UC_X86_REG_AX, (sc << 8) | asc)
                self.uc.reg_write(UC_X86_REG_EFLAGS, f & ~0x40)   # ZF=0
            else:
                self.uc.reg_write(UC_X86_REG_EFLAGS, f | 0x40)    # ZF=1
            return
        if ah in (0x00, 0x10):
            if self.key_buf:
                sc, asc = self.key_buf.popleft()
                self._set(UC_X86_REG_AX, (sc << 8) | asc)
            else:
                self._set(UC_X86_REG_AX, 0)
            return
        if ah == 0x02:
            self._set(UC_X86_REG_AX, 0)
            return

    def _dos(self):
        """Feed real keystrokes to the DOS console-input calls too.

        The README screen polls INT 21h AH=0Bh tens of thousands of times
        waiting for a key; without this it never advances.
        """
        ax = self._reg(UC_X86_REG_AX)
        ah = ax >> 8
        if ah == 0x0B:
            self.dos_counts[ah] += 1
            self._set(UC_X86_REG_AX, (ax & 0xFF00) |
                      (0xFF if self.key_buf else 0x00))
            self._cf(False)
            return
        if ah in (0x01, 0x06, 0x07, 0x08):
            self.dos_counts[ah] += 1
            if self.key_buf:
                sc, asc = self.key_buf.popleft()
                self._set(UC_X86_REG_AX, (ax & 0xFF00) | asc)
            else:
                self._set(UC_X86_REG_AX, ax & 0xFF00)
            self._cf(False)
            return
        return super()._dos()

    def _mouse(self):
        ax = self._reg(UC_X86_REG_AX)
        self.mouse_calls[ax] += 1
        if ax == 0x0000:
            self._set(UC_X86_REG_AX, 0xFFFF)
            self._set(UC_X86_REG_BX, 3)
            return
        if ax == 0x0003:
            x, y = self.mouse_pos
            self._set(UC_X86_REG_CX, x)
            self._set(UC_X86_REG_DX, y)
            self._set(UC_X86_REG_BX, self.mouse_btn)
            return
        if ax in (0x0005, 0x0006):
            x, y = self.mouse_pos
            self._set(UC_X86_REG_AX, self.mouse_btn)
            self._set(UC_X86_REG_BX, 1 if self.mouse_btn else 0)
            self._set(UC_X86_REG_CX, x)
            self._set(UC_X86_REG_DX, y)
            return
        if ax == 0x000B:
            dx, dy = self.mouse_rel
            self.mouse_rel = [0, 0]
            self._set(UC_X86_REG_CX, dx & 0xFFFF)
            self._set(UC_X86_REG_DX, dy & 0xFFFF)
            return
        if ax == 0x0004:
            self.mouse_pos = (self._reg(UC_X86_REG_CX),
                              self._reg(UC_X86_REG_DX))
            return
        return

    def _bios_video(self):
        ax = self._reg(UC_X86_REG_AX)
        ah, al = ax >> 8, ax & 0xFF
        self.int10_fn[ah] += 1
        if ah == 0x00:
            self.mode = al & 0x7F
            self.video_modes.append(self.mode)
            self.width, self.height = MODE_GEOM.get(self.mode, (320, 200))
            self.text_mode = self.mode in (0x00, 0x01, 0x02, 0x03, 0x07)
            print(f"  [vga] set mode {self.mode:#04x} -> "
                  f"{self.width}x{self.height} "
                  f"{'text' if self.text_mode else 'graphics'}")
        elif ah == 0x0F:                      # get current video mode
            self._set(UC_X86_REG_AX, (80 << 8) | self.mode)
            self._set(UC_X86_REG_BX, 0)
        elif ah == 0x10 and al == 0x12:       # set block of DAC registers
            first = self._reg(UC_X86_REG_BX)
            count = self._reg(UC_X86_REG_CX)
            es = self.uc.reg_read(UC_X86_REG_ES)
            dx = self._reg(UC_X86_REG_DX)
            blob = bytes(self.uc.mem_read(es * 16 + dx, count * 3))
            for i in range(count):
                r, g, b = blob[i * 3:i * 3 + 3]
                idx = (first + i) & 0xFF
                self.palette[idx] = (r * 255 // 63, g * 255 // 63,
                                     b * 255 // 63)
            self.palette_writes += count
        elif ah == 0x10 and al == 0x10:       # set single DAC register
            idx = self._reg(UC_X86_REG_BX) & 0xFF
            self.palette[idx] = (
                ((self._reg(UC_X86_REG_DX) >> 8) & 0x3F) * 255 // 63,
                (self._reg(UC_X86_REG_CX) >> 8 & 0x3F) * 255 // 63,
                (self._reg(UC_X86_REG_CX) & 0x3F) * 255 // 63)
            self.palette_writes += 1
        return

    # ------------------------------------------------------------ framebuffer
    def _on_plane_write(self, uc, access, address, size, value, user):
        """Shadow writes to the 0xa0000 aperture into four separate planes.

        In Mode X the CPU address selects a byte OFFSET and the sequencer map
        mask selects which of the four planes receive it, so several distinct
        pixels share one linear address. Unicorn's memory is flat and would let
        them overwrite each other, hence this shadow copy.
        """
        off = address - VGA_A000
        if off < 0 or off >= 0x10000:
            return
        planes, active = self.planes, self.active_planes
        if size == 1:
            b = value & 0xFF
            for p in active:
                planes[p][off] = b
        else:
            for i in range(size):
                b = (value >> (8 * i)) & 0xFF
                o = off + i
                if o < 0x10000:
                    for p in active:
                        planes[p][o] = b

    def framebuffer(self):
        w, h = self.width, self.height
        if self.chain4:
            return bytes(self.uc.mem_read(VGA_A000, w * h))
        # Mode X: interleave the four planes back into linear pixels.
        row_bytes = self.crtc_offset * 2 if self.crtc_offset else w // 4
        base = self.start_addr * 4 if self.start_mult == 4 else self.start_addr
        img = bytearray(w * h)
        span = w // 4
        for p in range(4):
            plane = self.planes[p]
            for y in range(h):
                src = base + y * row_bytes
                chunk = plane[src:src + span]
                if len(chunk) < span:          # ran off the end of the plane
                    if not self._warned_range:
                        self._warned_range = True
                        print(f"  [vga] !! start address {self.start_addr:#x} "
                              f"x{self.start_mult} = {base} puts row {y} at "
                              f"{src}, past the {len(plane)}-byte plane; "
                              f"frame would render black. Wrong addressing "
                              f"unit? (0x14={self.crtc.get(0x14, 0):#04x} "
                              f"0x17={self.crtc.get(0x17, 0):#04x})")
                    chunk = chunk + bytes(span - len(chunk))
                img[y * w + p:y * w + w:4] = chunk
        if len(img) != w * h:                  # never expected; keep the caller safe
            print(f"  [vga] !! framebuffer {len(img)} != {w * h} "
                  f"(w={w} h={h} row_bytes={row_bytes} base={base:#x})")
            img = (bytes(img) + bytes(w * h))[:w * h]
        return bytes(img)

    def vga_state(self):
        return {
            "mode": f"{self.mode:#04x}",
            "geometry": f"{self.width}x{self.height}",
            "chain4": self.chain4,
            "map_mask": f"{self.map_mask:#03x}",
            "start_addr": f"{self.start_addr:#x}",
            "start_mult": self.start_mult,
            "crtc_offset": self.crtc_offset,
            "row_bytes": self.crtc_offset * 2 if self.crtc_offset
                         else self.width // 4,
            "crtc_regs": {f"{k:#02x}": f"{v:#02x}"
                          for k, v in sorted(self.crtc.items())},
            "dac_writes": self.palette_writes,
            "nonblack_palette": sum(1 for c in self.palette if c != (0, 0, 0)),
            "plane_nonzero": [sum(1 for b in pl[:16000] if b)
                              for pl in self.planes],
            "aperture_nonzero": sum(
                1 for b in bytes(self.uc.mem_read(VGA_A000, 16000)) if b),
        }

    def textbuffer(self):
        """80x25 character/attribute pairs from the text-mode framebuffer."""
        base = VGA_B000 if self.mode == 0x07 else VGA_B800
        return bytes(self.uc.mem_read(base, 80 * 25 * 2))


# Standard CGA/EGA 16-colour attribute palette.
CGA16 = [
    (0, 0, 0), (0, 0, 170), (0, 170, 0), (0, 170, 170),
    (170, 0, 0), (170, 0, 170), (170, 85, 0), (170, 170, 170),
    (85, 85, 85), (85, 85, 255), (85, 255, 85), (85, 255, 255),
    (255, 85, 85), (255, 85, 255), (255, 255, 85), (255, 255, 255),
]


def render_text(m, font, cell_w, cell_h):
    """Draw the text-mode screen. Ducks shows its README here before the game."""
    surf = pygame.Surface((80 * cell_w, 25 * cell_h))
    surf.fill(CGA16[0])
    buf = m.textbuffer()
    for row in range(25):
        for col in range(80):
            i = (row * 80 + col) * 2
            ch, attr = buf[i], buf[i + 1]
            bg = CGA16[(attr >> 4) & 0x07]
            fg = CGA16[attr & 0x0F]
            rect = pygame.Rect(col * cell_w, row * cell_h, cell_w, cell_h)
            if bg != CGA16[0]:
                surf.fill(bg, rect)
            if ch not in (0, 32, 255):
                glyph = font.render(CP437[ch], False, fg, bg)
                surf.blit(glyph, rect.topleft)
    return surf


# CP437 -> unicode for the printable range plus the box-drawing glyphs DOS
# programs use for framing. Anything unmapped renders as a space.
CP437 = [" "] * 256
for _i in range(32, 127):
    CP437[_i] = chr(_i)
for _i, _c in zip(
        range(176, 224),
        "░▒▓│┤╡╢╖╕╣║╗╝╜╛┐└┴┬├─┼╞╟╚╔╩╦╠═╬╧╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀"):
    CP437[_i] = _c
CP437[249] = "·"
CP437[250] = "·"
CP437[254] = "■"
CP437[7] = "•"
CP437[15] = "☼"
CP437[16] = "►"
CP437[17] = "◄"
CP437[24] = "↑"
CP437[25] = "↓"
CP437[26] = "→"
CP437[27] = "←"

VGA_B800 = 0xB8000
VGA_B000 = 0xB0000


def make_surface(m, font=None, cell=(8, 16)):
    if m.text_mode and font is not None:
        return render_text(m, font, *cell)
    buf = m.framebuffer()
    w, h = m.width, m.height
    if len(buf) != w * h:
        print(f"  [vga] !! buffer {len(buf)} bytes but {w}x{h} needs {w * h}")
        buf = (buf + bytes(w * h))[:w * h]
    surf = pygame.image.frombuffer(buf, (w, h), "P")
    surf.set_palette(m.palette)
    return surf


def capture(m, screen, tag, outdir="debug"):
    """Dump everything needed to debug the display off-line."""
    import json
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, tag)

    pygame.image.save(screen, f"{base}_window.png")
    if not m.text_mode:
        raw = make_surface(m)
        pygame.image.save(raw.convert(24), f"{base}_raw.png")
        # Raw planes + palette, so alternative interpretations can be tried
        # without having to reach this point in the game again.
        with open(f"{base}_planes.bin", "wb") as f:
            for pl in m.planes:
                f.write(pl)
        with open(f"{base}_aperture.bin", "wb") as f:
            f.write(bytes(m.uc.mem_read(VGA_A000, 0x10000)))
    else:
        with open(f"{base}_text.txt", "w") as f:
            buf = m.textbuffer()
            for row in range(25):
                f.write("".join(CP437[buf[(row * 80 + c) * 2]]
                                for c in range(80)).rstrip() + "\n")
    with open(f"{base}_palette.bin", "wb") as f:
        for c in m.palette:
            f.write(bytes(c))
    state = m.vga_state()
    state["cs:ip"] = (f"{m._reg(UC_X86_REG_CS):04x}:"
                      f"{m._reg(UC_X86_REG_IP):04x}")
    state["elapsed"] = round(m._elapsed(), 2)
    state["files_read"] = m.files_read
    with open(f"{base}_state.json", "w") as f:
        json.dump(state, f, indent=2)
    print(f"  [capture] {base}_*  state={json.dumps(state)}")
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="../Ducks.exe")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--blaster", action="store_true")
    ap.add_argument("--chunk", type=int, default=400_000,
                    help="instructions to run between display updates")
    ap.add_argument("--shots", type=int, default=0,
                    help="save this many PNG frames then exit (headless)")
    ap.add_argument("--shot-every", type=float, default=1.5,
                    help="seconds between saved frames")
    ap.add_argument("--shot-dir", default="shots")
    ap.add_argument("--status-every", type=float, default=5.0)
    args = ap.parse_args()

    headless = args.shots > 0
    if headless:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.makedirs(args.shot_dir, exist_ok=True)

    pygame.init()
    m = VgaDos(args.exe, blaster=args.blaster, max_insns=1 << 62)
    print(f"=== running {args.exe} "
          f"(BLASTER {'set' if args.blaster else 'unset'}) ===")
    print("    host filesystem READ-ONLY; writes intercepted in memory")

    pygame.font.init()
    CELL = (8, 16)
    fpath = pygame.font.match_font("dejavusansmono,liberationmono,monospace")
    font = pygame.font.Font(fpath, 13) if fpath \
        else pygame.font.SysFont(None, 16)

    def base_size():
        return (80 * CELL[0], 25 * CELL[1]) if m.text_mode \
            else (m.width, m.height)

    bw, bh = base_size()
    win_w, win_h = bw * args.scale, bh * args.scale
    screen = pygame.display.set_mode((win_w, win_h))
    pygame.display.set_caption("Ducks! v1.2 - unicorn/SDL")
    clock = pygame.time.Clock()

    cs = m._reg(UC_X86_REG_CS)
    ip = m._reg(UC_X86_REG_IP)
    addr = cs * 16 + ip
    running = True
    shots_taken = 0
    next_shot = args.shot_every
    next_status = args.status_every
    frames = 0
    paused = False
    cap_n = 0
    print("    controls: F9 pause/resume, F10 capture, F11 cycle Mode X "
          "start multiplier, F12 quit")
    print("    or from a shell: touch capture.request / touch pause.request")

    while running:
        if not paused:
            try:
                m.uc.emu_start(addr, 0, count=args.chunk)
            except UcError as e:
                print(f"  [cpu] {e} at {m._reg(UC_X86_REG_CS):04x}:"
                      f"{m._reg(UC_X86_REG_IP):04x}")
                running = False
            if m.finished:
                print(f"  [dos] program exited: {m.finished}")
                running = False
            addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F12:
                    running = False
                elif ev.key == pygame.K_F9:
                    paused = not paused
                    print(f"  [ctl] {'PAUSED' if paused else 'resumed'} "
                          f"at {m._reg(UC_X86_REG_CS):04x}:"
                          f"{m._reg(UC_X86_REG_IP):04x}")
                elif ev.key == pygame.K_F10:
                    cap_n += 1
                    capture(m, screen, f"cap{cap_n:02d}")
                elif ev.key == pygame.K_F11:
                    # Cycle candidate Mode X interpretations while paused, so a
                    # wrong guess can be identified without replaying the game.
                    m.start_mult = 1 if m.start_mult == 4 else 4
                    print(f"  [ctl] start_mult -> {m.start_mult}")
                else:
                    mapped = KEYMAP.get(ev.key)
                    if mapped:
                        m.key_buf.append(mapped)
                        m.last_scancode = mapped[0]
            elif ev.type == pygame.KEYUP:
                mapped = KEYMAP.get(ev.key)
                if mapped:
                    m.last_scancode = mapped[0] | 0x80
            elif ev.type == pygame.MOUSEMOTION:
                mx, my = ev.pos
                # INT 33h reports in a virtual 640x200 space for mode 13h.
                m.mouse_pos = (int(mx / win_w * 640), int(my / win_h * 200))
                m.mouse_rel[0] += ev.rel[0]
                m.mouse_rel[1] += ev.rel[1]
            elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                b = pygame.mouse.get_pressed()
                m.mouse_btn = (1 if b[0] else 0) | (2 if b[2] else 0) \
                    | (4 if b[1] else 0)

        # File-based control, so a capture can be requested from outside the
        # window: `touch capture.request` / `touch pause.request`.
        if os.path.exists("capture.request"):
            os.remove("capture.request")
            cap_n += 1
            capture(m, screen, f"cap{cap_n:02d}")
        if os.path.exists("pause.request"):
            os.remove("pause.request")
            paused = not paused
            print(f"  [ctl] {'PAUSED' if paused else 'resumed'} by request "
                  f"at {m._reg(UC_X86_REG_CS):04x}:"
                  f"{m._reg(UC_X86_REG_IP):04x}")

        nb = base_size()
        if nb != (bw, bh):
            bw, bh = nb
            win_w, win_h = bw * args.scale, bh * args.scale
            screen = pygame.display.set_mode((win_w, win_h))

        # Convert the 8-bit palettised surface to the display format before
        # scaling: transform.scale needs matching source/destination formats.
        surf = make_surface(m, font, CELL).convert(screen)
        pygame.transform.scale(surf, (win_w, win_h), screen)
        pygame.display.flip()
        frames += 1

        el = m._elapsed()
        if True:
            if el >= next_status:
                fb = m.framebuffer()
                print(f"  [stat] t={el:6.1f}s  "
                      f"cs:ip={m._reg(UC_X86_REG_CS):04x}:"
                      f"{m._reg(UC_X86_REG_IP):04x}  "
                      f"mode={m.mode:#04x} dac={m.palette_writes} "
                      f"int10={m.int_counts[0x10]} int33={m.int_counts[0x33]} "
                      f"int21={m.int_counts[0x21]} "
                      f"fb_nonzero={sum(1 for b in fb[:8000] if b)} "
                      f"out3c9={m.port_out.get(0x3C9, 0)} "
                      f"in3da={m.port_in.get(0x3DA, 0)} "
                      f"chain4={m.chain4} start={m.start_addr:#x} "
                      f"crtc_off={m.crtc_offset} "
                      f"vde={m.crtc.get(0x12, 0):#x}")
                next_status += args.status_every
                if m.text_mode:
                    buf = m.textbuffer()
                    chars = sum(1 for i in range(0, len(buf), 2)
                                if buf[i] not in (0, 32))
                    print(f"  [text] {chars} non-blank cells at "
                          f"{'0xb8000' if m.mode != 7 else '0xb0000'}")
                    for row in range(25):
                        line = "".join(
                            CP437[buf[(row * 80 + c) * 2]] for c in range(80))
                        if line.strip():
                            print(f"    |{line.rstrip()}")
            if args.shots and el >= next_shot:
                path = os.path.join(args.shot_dir, f"frame{shots_taken:02d}.png")
                pygame.image.save(screen, path)
                nz = sum(1 for c in m.palette if c != (0, 0, 0))
                print(f"  [shot] {path}  t={el:5.1f}s  "
                      f"mode={m.mode:#04x} palette_entries={nz} "
                      f"dac_writes={m.palette_writes}")
                shots_taken += 1
                next_shot += args.shot_every
                if shots_taken >= args.shots:
                    running = False
        if not headless:
            clock.tick(60)

    print(f"\n=== finished after {frames} display updates, "
          f"{m._elapsed():.1f}s ===")
    print(f"  video modes set : {[hex(v) for v in m.video_modes]}")
    print(f"  DAC palette sets: {m.palette_writes}")
    print(f"  INT 10h funcs   : "
          f"{{{', '.join(f'AH={a:02x}h:{c}' for a, c in m.int10_fn.most_common(12))}}}")
    print(f"  INT 21h funcs   : "
          f"{{{', '.join(f'AH={a:02x}h:{c}' for a, c in m.dos_counts.most_common(12))}}}")
    print(f"  INT 33h funcs   : "
          f"{{{', '.join(f'AX={a:04x}:{c}' for a, c in m.mouse_calls.most_common(12))}}}")
    print(f"  video-mem writes: "
          f"{{{', '.join(f'{k}:{v}' for k, v in sorted(m.vidwrites.items()))}}}")
    for k, (lo, hi) in sorted(m.vidrange.items()):
        print(f"    {k} address range {lo:#07x}..{hi:#07x}")
    # Where in the whole video aperture is there actually non-zero content?
    ap = bytes(m.uc.mem_read(0xA0000, 0x20000))
    runs, inrun = [], None
    for i in range(0, len(ap), 512):
        blk = any(ap[i:i + 512])
        if blk and inrun is None:
            inrun = i
        elif not blk and inrun is not None:
            runs.append((0xA0000 + inrun, 0xA0000 + i))
            inrun = None
    if inrun is not None:
        runs.append((0xA0000 + inrun, 0xA0000 + len(ap)))
    print(f"  non-zero video regions: "
          f"{[f'{a:#07x}..{b:#07x}' for a, b in runs] or 'none'}")
    print(f"  OUT ports       : "
          f"{{{', '.join(f'{p:#05x}:{c}' for p, c in m.port_out.most_common(14))}}}")
    print(f"  files read      : {m.files_read}")
    print(f"  files written   : {m.files_written} (intercepted)")
    print(f"  files missing   : {m.files_missing}")
    if m.stdout:
        print("  console output  :")
        for line in m.stdout.decode('latin1').replace('\r', '').split('\n'):
            print(f"    | {line}")
    pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
