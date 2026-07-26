#!/usr/bin/env python3
"""
Native-I/O port of Ducks: keep the game logic on the emulated CPU, but service
its I/O by calling pygame directly instead of emulating hardware.

`emulation.py` models the machine - VGA registers, DMA, DSP, interrupts - and
the game's own driver code runs against it. That is faithful, and it stays the
reference we fall back on whenever a result is in doubt, but everything goes
through per-instruction and per-write Python callbacks.

Here the interception moves up a level. Ducks is a Borland Turbo C++ program, so
its I/O sits behind ordinary C functions with a regular calling convention. If we
recognise one of those functions we can skip its body entirely: read the
arguments off the stack, do the work natively, put the result in AX, and return
to the caller. Anything not yet recognised still falls through to the full
emulation underneath, so the port can proceed one routine at a time and is never
in a broken state.

Two pieces are needed, and discovery has to come first - you cannot replace a
routine you have not identified:

    python native.py --profile      # find the hot I/O routines and name them
    python native.py                # run with whatever natives are registered

Usage mirrors emulation.py otherwise (--scale, --blaster, F9/F10/F12).
"""
import argparse
import os
import struct
import sys
from collections import Counter, defaultdict

import pygame
from unicorn import *
from unicorn.x86_const import *

import emulation
from emulation import VgaDos, make_surface, capture

# Borland large-model layout, as established in emulation.py and analyze.py.
DGROUP_IMAGE_OFF = 0x18950


class Native(VgaDos):
    """VgaDos plus a table of game functions serviced natively."""

    def __init__(self, *a, profile=False, keep_diagnostics=False,
                 verify=False, **kw):
        self.natives = {}            # image offset -> (name, handler, kind)
        self.native_calls = Counter()
        self.profiling = profile
        self.draw_sites = Counter()  # CS:IP -> bytes written to video memory
        self.native_pixels = 0
        self.verify = verify
        self.verify_calls = 0
        self.verify_bad = 0
        super().__init__(*a, **kw)
        self.image_base = self.load_seg * 16
        self.dgroup_base = self.image_base + DGROUP_IMAGE_OFF
        # Drop the inherited diagnostics unless explicitly profiling. They fire
        # per video write and per sound-buffer write and measure nothing this
        # port needs; only the Mode X plane shadowing has to stay.
        if not (profile or keep_diagnostics):
            self.uc.hook_del(self._vidwrite_hook)
            self._diagnostics_off = True
        self._profile_hook = None
        if self.profiling:
            self.enable_profiling()
        self._register_natives()
        if self.natives:
            for off in self.natives:
                lin = self.image_base + off
                self.uc.hook_add(UC_HOOK_CODE, self._on_native, None, lin, lin)

    def _watch_dma_buffer(self):
        """Suppress the sound-buffer write watch; it is diagnostics only."""
        if getattr(self, "_diagnostics_off", False):
            return
        return super()._watch_dma_buffer()

    # ------------------------------------------------------------- profiling
    def enable_profiling(self, reset=True):
        """Start attributing video writes to code sites.

        Toggleable at runtime so a specific slow moment can be measured without
        paying the per-write callback for the whole session - and without having
        to replay the game to reach that moment again.
        """
        if self._profile_hook is not None:
            return False
        if reset:
            self.draw_sites.clear()
        self._profile_hook = self.uc.hook_add(
            UC_HOOK_MEM_WRITE, self._on_draw_write, None, 0xA0000, 0xAFFFF)
        self.profiling = True
        return True

    def disable_profiling(self):
        if self._profile_hook is None:
            return False
        self.uc.hook_del(self._profile_hook)
        self._profile_hook = None
        self.profiling = False
        return True

    def _on_draw_write(self, uc, access, address, size, value, user):
        self.draw_sites[(uc.reg_read(UC_X86_REG_CS),
                         uc.reg_read(UC_X86_REG_IP))] += size

    def profile_report(self, img):
        """Map hot video-memory writers back to the functions containing them."""
        print("\n=== video-memory write sites (candidates to replace) ===")
        by_func = defaultdict(int)
        detail = {}
        for (cs, ip), n in self.draw_sites.most_common(40):
            off = cs * 16 + ip - self.image_base
            fn = find_function_start(img, off)
            by_func[fn] += n
            detail.setdefault(fn, []).append((off, n))
        print(f"{'function':>10}  {'bytes':>10}  write sites")
        for fn, n in sorted(by_func.items(), key=lambda kv: -kv[1]):
            sites = ", ".join(f"{o:#07x}({c})" for o, c in detail[fn][:4])
            name = f"{fn:#07x}" if fn is not None else "  unknown"
            print(f"{name:>10}  {n:>10}  {sites}")
        print("\nReplace the functions at the top of that list first: their "
              "bodies are\nwhere the per-pixel emulation cost is concentrated.")

    # --------------------------------------------------------------- natives
    def _register_natives(self):
        """Install the natively-serviced functions.

        Kept deliberately small to begin with. Each entry is
        (image offset, name, handler, return kind), where the handler receives
        this object and the argument base address on the stack.
        """
        for off, name, fn, kind in NATIVE_TABLE:
            self.natives[off] = (name, fn, kind)

    def _on_native(self, uc, address, size, user):
        off = address - self.image_base
        entry = self.natives.get(off)
        if entry is None:
            return
        name, handler, kind = entry
        self.native_calls[name] += 1

        if self.verify:
            # Run the native into a scratch copy, then let the original body
            # run and compare. A hand-translated blitter can be subtly wrong in
            # ways that look plausible on screen, and emulation.py is the only
            # authority on what the right answer is.
            return self._verify_native(uc, off, name, handler, kind)

        ss = self._reg(UC_X86_REG_SS)
        sp = self._reg(UC_X86_REG_SP)
        # At function entry the stack holds the return address, then the
        # arguments. Borland's large model pushes CS too, whether via a real far
        # call or the `push cs; call near` idiom, so both look the same here.
        ret_size = 4 if kind == "far" else 2
        args_at = ss * 16 + sp + ret_size

        result = handler(self, args_at)

        if result is not None:
            if isinstance(result, tuple):
                ax, dx = result
                self._set(UC_X86_REG_DX, dx)
            else:
                ax = result
            self._set(UC_X86_REG_AX, ax)

        # Return to the caller, skipping the original body entirely.
        ip = struct.unpack("<H", uc.mem_read(ss * 16 + sp, 2))[0]
        if ret_size == 4:
            cs = struct.unpack("<H", uc.mem_read(ss * 16 + sp + 2, 2))[0]
            uc.reg_write(UC_X86_REG_CS, cs)
        self._set(UC_X86_REG_SP, sp + ret_size)
        uc.reg_write(UC_X86_REG_IP, ip)

    def _verify_native(self, uc, off, name, handler, kind):
        """Compare a native against the code it replaces, on a live call.

        Snapshot the planes, let the native write into that snapshot, restore,
        then let the original body run. A one-shot hook on the return address
        diffs the two once the real code is done.
        """
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
        ret_ip = struct.unpack("<H", uc.mem_read(ss * 16 + sp, 2))[0]
        ret_cs = struct.unpack("<H", uc.mem_read(ss * 16 + sp + 2, 2))[0] \
            if kind == "far" else self._reg(UC_X86_REG_CS)
        args_at = ss * 16 + sp + (4 if kind == "far" else 2)

        before = [bytes(p) for p in self.planes]
        try:
            handler(self, args_at)
        except Exception as e:
            print(f"  [verify] {name}: native raised {e!r}")
            for i, p in enumerate(before):
                self.planes[i][:] = p
            return
        predicted = [bytes(p) for p in self.planes]
        for i, p in enumerate(before):
            self.planes[i][:] = p           # undo; the original will redo it

        ret_lin = ret_cs * 16 + ret_ip
        state = {"h": None}

        def on_return(uc2, addr2, size2, user2):
            if addr2 != ret_lin:
                return
            uc2.hook_del(state["h"])
            diffs = 0
            first = None
            for pi in range(4):
                a, b = predicted[pi], self.planes[pi]
                for j in range(len(a)):
                    if a[j] != b[j]:
                        diffs += 1
                        if first is None:
                            first = (pi, j, a[j], b[j])
            self.verify_calls += 1
            if diffs:
                self.verify_bad += 1
                print(f"  [verify] {name}: MISMATCH {diffs} bytes, first "
                      f"plane{first[0]} off {first[1]:#07x} "
                      f"native={first[2]:#04x} real={first[3]:#04x}")
            elif self.verify_calls <= 5 or self.verify_calls % 200 == 0:
                print(f"  [verify] {name}: match #{self.verify_calls}")

        state["h"] = uc.hook_add(UC_HOOK_CODE, on_return, None,
                                 ret_lin, ret_lin)
        # Fall through: do NOT skip the body, the real code must run.

    # ------------------------------------------------------------- utilities
    def arg16(self, base, i):
        return struct.unpack("<H", self.uc.mem_read(base + i * 2, 2))[0]

    def arg32(self, base, i):
        return struct.unpack("<I", self.uc.mem_read(base + i * 2, 4))[0]

    def read(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))

    def write(self, addr, data):
        self.uc.mem_write(addr, bytes(data))


def find_function_start(img, off, limit=0x600):
    """Scan back for a Borland function prologue (push bp; mov bp,sp)."""
    for back in range(0, limit):
        i = off - back
        if i < 2:
            break
        if img[i] == 0x55 and img[i + 1] == 0x8B and img[i + 2] == 0xEC:
            return i
        # Also accept the retf/ret that ends the previous function, meaning the
        # next byte begins this one.
        if img[i] in (0xCB, 0xC3) and img[i + 1] == 0x55:
            return i + 1
    return None


# ---------------------------------------------------------------- natives ---
# Each handler is given (machine, args_at) and returns AX, or (AX, DX), or None.

def native_snow_blit(m, args):
    """Replace the CGA snow-avoidance text blit with a straight copy.

    The original waits for a horizontal-blank transition on port 0x3da for every
    single word it copies - roughly 3 port reads per 2 bytes, all of which cost
    a Python callback here and buy nothing on a machine with no CGA in it.
    """
    # void blit(dst_off, dst_seg, src_off, src_seg, words)  [caller cleans 10]
    dst_off, dst_seg = m.arg16(args, 0), m.arg16(args, 1)
    src_off, src_seg = m.arg16(args, 2), m.arg16(args, 3)
    words = m.arg16(args, 4)
    if words:
        data = m.read(src_seg * 16 + src_off, words * 2)
        m.write(dst_seg * 16 + dst_off, data)
    return None


def native_compose_layer(m, args):
    """Native replacement for the background compositor at 0x05d3a.

    Takes no stack arguments - every input is a DGROUP global:

        [0x16f5] far ptr -> array of far row pointers (foreground)
        [0x170b] far ptr -> array of far row pointers (background)
        [0x16f1]/[0x16f3] destination far pointer
        [0x1727]          destination row offset
        [0x538]/[0x53a]   width limit / height
        [0x177d]/[0x177f] x / y scroll offsets
        [0x1729]/[0x172b] wrap masks for the background lookup

    Per pixel it takes the foreground byte, falling back to the background tile
    where that byte is zero (transparent). Column steps by 4, so one call fills
    a single Mode X plane; the write goes straight into the plane shadow.
    """
    g = m.dgroup_base
    u16 = lambda o: struct.unpack("<H", m.read(g + o, 2))[0]

    def farptr(o):
        off, seg = struct.unpack("<HH", m.read(g + o, 4))
        return seg * 16 + off

    fg_table = farptr(0x16F5)
    bg_table = farptr(0x170B)
    dst_seg, dst_off = u16(0x16F3), u16(0x16F1) + u16(0x1727)
    width, height = u16(0x538), u16(0x53A)
    x0, y0 = m.read(g + 0x177D, 1)[0], m.read(g + 0x177F, 1)[0]
    mask_x, mask_y = u16(0x1729), u16(0x172B)

    dst_lin = dst_seg * 16 + dst_off
    plane_off = dst_lin - 0xA0000
    if plane_off < 0 or not m.active_planes:
        return None                       # not drawing into the Mode X aperture

    out = bytearray()
    for row in range(height):
        by = (row + y0) & mask_y
        fg_row_ptr = struct.unpack(
            "<HH", m.read(fg_table + row * 4, 4))
        fg_lin = fg_row_ptr[1] * 16 + fg_row_ptr[0]
        bg_row_ptr = struct.unpack("<HH", m.read(bg_table + by * 4, 4))
        bg_lin = bg_row_ptr[1] * 16 + bg_row_ptr[0]
        cols = list(range(x0, width, 4))
        fg = m.read(fg_lin + x0, max(0, width - x0))
        bg = m.read(bg_lin, mask_x + 1)
        for sx in cols:
            v = fg[sx - x0]
            if v == 0:
                v = bg[sx & mask_x]
            out.append(v)

    for p in m.active_planes:
        m.planes[p][plane_off:plane_off + len(out)] = out
    m.native_pixels += len(out)
    return None


def native_draw_sprite(m, args):
    """Native replacement for the clipped sprite blitter at 0x063d6.

    Signature reconstructed from the disassembly (far call, caller cleans):

        [+0x06] far ptr -> word: index into the sprite table
        [+0x0a] word    : x
        [+0x0c] dword   : y
        [+0x10] far ptr -> table header; [2]=descriptor base, [4]=segment
        [+0x14] far ptr -> viewport: [0]=top, [2]=bottom, [4]=plane base,
                           [6]=y bias, [8]=right
        [+0x18] byte    : colour offset added to every pixel (the highlight)

    A 14-byte descriptor gives width, height, origin and a far pointer to the
    pixels. The original writes only pixels whose `x & 3` equals the current
    plane in [0x177d], so the game calls it four times per sprite; here the
    whole span is done in one pass over the plane shadow.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    clip = far(args + 0x14 - 6)
    table = far(args + 0x10 - 6)
    index = u16(far(args + 0x06 - 6))
    desc = u16(table + 4) * 16 + u16(table + 2) + index * 14

    w, h = u16(desc + 0), u16(desc + 2)
    ox, oy = s16(desc + 4), s16(desc + 6)
    pixels = far(desc + 0x0A)
    colour = m.read(args + 0x18 - 6, 1)[0]

    x = s16(args + 0x0A - 6) - ox
    y = struct.unpack("<i", m.read(args + 0x0C - 6, 4))[0] + s16(clip + 0) - oy

    src = 0            # index into the sprite's pixel data
    row_extra = 0      # per-row source advance added by horizontal clipping
    x_end, y_end = x + w, y + h

    if x < 0:
        row_extra -= x
        src -= x
        x = 0
    elif s16(clip + 8) < x_end:
        row_extra += x_end - s16(clip + 8)
        x_end = s16(clip + 8)

    top = s16(clip + 0)
    if top > y:
        src += (top - y) * w
        y = top
    elif s16(clip + 2) < y_end:
        y_end = s16(clip + 2)

    if x_end <= x or y_end <= y:
        return None

    stride = 90 if u16(g + 0x4FE) else 80
    base = u16(g + 0x1727) + (m.read(g + 0x177D, 1)[0] if
                              u16(g + 0x4FE) else 0)
    dst_lin = far(g + 0x16F1)
    plane_off = dst_lin - 0xA0000
    if plane_off < 0 or not m.active_planes:
        return None

    plane = m.read(g + 0x177D, 1)[0] & 3
    data = m.read(pixels, w * h + src + 1) if w and h else b""
    planes = [m.planes[p] for p in m.active_planes]
    drawn = 0
    for row in range(y, y_end):
        rowbase = plane_off + base + row * stride
        i = src
        for sx in range(x, x_end):
            if i < len(data):
                v = data[i]
                if v and (sx & 3) == plane:
                    o = rowbase + (sx >> 2)
                    for pl in planes:
                        if 0 <= o < len(pl):
                            pl[o] = (v + colour) & 0xFF
                    drawn += 1
            i += 1
        src = i + row_extra
    m.native_pixels += drawn
    return None


def native_blit_rows(m, args):
    """Native replacement for the row blitter at 0x05c09.

        [+0x06] far ptr -> far ptr -> array of far row pointers
        [+0x0a] word    : first destination row
        [+0x0c] word    : last destination row (exclusive)
        [+0x0e] word    : first x
        [+0x10] word    : last x (exclusive)
        [+0x1e] word    : first source row index

    No transparency test - it copies unconditionally. Source steps 4 bytes per
    pixel (staying within one plane) while the destination steps 1, so each row
    is a strided gather into a contiguous run, which Python slicing does in one
    go instead of one emulated iteration per pixel.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    table = far(far(args + 0x06 - 6))
    row0, row1 = u16(args + 0x0A - 6), u16(args + 0x0C - 6)
    x0, x1 = u16(args + 0x0E - 6), u16(args + 0x10 - 6)
    srcrow = u16(args + 0x1E - 6)

    plane = m.read(g + 0x177D, 1)[0]
    stride = 90 if u16(g + 0x4FE) else 80
    dst_lin = far(g + 0x16F1)
    plane_off = dst_lin - 0xA0000 + u16(g + 0x1727)
    if plane_off < 0 or not m.active_planes:
        return None

    sx = x0 + plane
    n = max(0, (x1 - sx + 3) // 4)
    if n == 0:
        return None
    planes = [m.planes[p] for p in m.active_planes]
    for row in range(row0, row1):
        src = far(table + srcrow * 4) + plane
        data = m.read(src, n * 4)[::4]
        o = plane_off + row * stride + (sx >> 2)
        for pl in planes:
            if 0 <= o and o + len(data) <= len(pl):
                pl[o:o + len(data)] = data
        m.native_pixels += len(data)
        srcrow += 1
    return None


# Offsets are into the unpacked image; confirmed against the disassembly and
# ranked by --profile. Anything not listed still runs on the emulated CPU.
# Verify a new entry with --verify before trusting it.
NATIVE_TABLE = [
    (0x05D3A, "compose_layer", native_compose_layer, "far"),
    (0x063D6, "draw_sprite", native_draw_sprite, "far"),
    (0x05C09, "blit_rows", native_blit_rows, "far"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="../Ducks.exe")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--chunk", type=int, default=400_000)
    ap.add_argument("--blaster", action="store_true")
    ap.add_argument("--profile", action="store_true",
                    help="report which routines do the drawing, then exit")
    ap.add_argument("--verify", action="store_true",
                    help="run each native alongside the code it replaces and "
                         "diff the result (slow; correctness check only)")
    ap.add_argument("--keep-diagnostics", action="store_true",
                    help="keep the inherited per-write instrumentation on")
    ap.add_argument("--status-every", type=float, default=30.0)
    ap.add_argument("--unpacked", default="Ducks.unpacked.exe",
                    help="unpacked image, used to name functions when profiling")
    args = ap.parse_args()

    pygame.init()
    pygame.font.init()
    m = Native(args.exe, blaster=args.blaster, profile=args.profile,
               keep_diagnostics=args.keep_diagnostics, verify=args.verify,
               max_insns=1 << 62)
    # Kept in hand so the profile report can name functions at any moment.
    _d = open(args.unpacked, "rb").read()
    img = _d[struct.unpack_from("<13H", _d, 2)[3] * 16:]
    print(f"=== native-I/O port: {len(m.natives)} routine(s) serviced "
          f"natively, everything else emulated ===")

    fpath = pygame.font.match_font("dejavusansmono,liberationmono,monospace")
    font = pygame.font.Font(fpath, 13) if fpath else pygame.font.SysFont(None, 16)
    CELL = (8, 16)

    def base_size():
        return (80 * CELL[0], 25 * CELL[1]) if m.text_mode \
            else (m.width, m.height)

    bw, bh = base_size()
    screen = pygame.display.set_mode((bw * args.scale, bh * args.scale))
    pygame.display.set_caption("Ducks! - native I/O")
    clock = pygame.time.Clock()

    addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
    running, frames, next_status = True, 0, args.status_every
    while running:
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
        m.service_sound()
        addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT or (
                    ev.type == pygame.KEYDOWN and ev.key == pygame.K_F12):
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F5:
                    on = m.enable_profiling() or not m.disable_profiling()
                    print(f"  [trace] {'ON (counters reset)' if m.profiling else 'OFF'}")
                elif ev.key == pygame.K_F6:
                    m.profile_report(img)
                else:
                    mapped = emulation.KEYMAP.get(ev.key)
                    if mapped:
                        m.key_buf.append(mapped)
                        m.last_scancode = mapped[0]
            elif ev.type == pygame.KEYUP:
                mapped = emulation.KEYMAP.get(ev.key)
                if mapped:
                    m.last_scancode = mapped[0] | 0x80
            elif ev.type == pygame.MOUSEMOTION:
                k = m.mouse_sens / args.scale
                m.mouse_rel[0] += ev.rel[0] * k
                m.mouse_rel[1] += ev.rel[1] * k
            elif ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                idx = {1: 0, 3: 1, 2: 2}.get(ev.button)
                if idx is not None:
                    bit = 1 << idx
                    if ev.type == pygame.MOUSEBUTTONDOWN:
                        m.mouse_btn |= bit
                        m.press_count[idx] += 1
                        m.press_pos[idx] = m.mouse_pos
                    else:
                        m.mouse_btn &= ~bit
                        m.release_count[idx] += 1
                        m.release_pos[idx] = m.mouse_pos

        # Shell-side control, so tracing can be driven without window focus.
        for name, action in (("trace.on", "on"), ("trace.off", "off"),
                             ("trace.report", "report")):
            if os.path.exists(name):
                os.remove(name)
                if action == "on":
                    m.enable_profiling()
                    print("  [trace] ON (counters reset)")
                elif action == "off":
                    m.disable_profiling()
                    print("  [trace] OFF")
                else:
                    m.profile_report(img)

        nb = base_size()
        if nb != (bw, bh):
            bw, bh = nb
            screen = pygame.display.set_mode((bw * args.scale, bh * args.scale))
        surf = make_surface(m, font, CELL).convert(screen)
        pygame.transform.scale(surf, screen.get_size(), screen)
        pygame.display.flip()
        frames += 1
        clock.tick(60)

        if m._elapsed() >= next_status:
            next_status += args.status_every
            print(f"  [stat] t={m._elapsed():6.1f}s frames={frames} "
                  f"mode={m.mode:#04x} natives={dict(m.native_calls)}")

    print(f"\n=== finished after {frames} frames, {m._elapsed():.1f}s ===")
    if m.native_calls:
        print(f"  native calls    : {dict(m.native_calls)}")
        print(f"  pixels drawn natively: {m.native_pixels}")
    if m.verify:
        print(f"  verify          : {m.verify_calls} calls compared, "
              f"{m.verify_bad} MISMATCHED")
    if m.draw_sites:
        m.profile_report(img)
    pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
