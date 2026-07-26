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
import time
from collections import Counter, defaultdict

import numpy as np
import pygame
from unicorn import *
from unicorn.x86_const import *

import emulation
from emulation import VgaDos, make_surface, capture, AudioSink

# Borland large-model layout, as established in emulation.py and analyze.py.
DGROUP_IMAGE_OFF = 0x18950

# Natives excluded from --verify. All of the graphics routines are currently
# skipped: they have been exercised enough in practice, and comparing them costs
# a lot for little remaining information. To re-check one, delete it from this
# set and run with --verify (or press F7 mid-run).
VERIFY_SKIP = {
    "plot_pixel",
    "blit_rows",
    "blit_rows_masked",
    "compose_layer",
    "compose_scroll",
    "draw_sprite",
    "clear_vram",
}


class Native(VgaDos):
    """VgaDos plus a table of game functions serviced natively."""

    def __init__(self, *a, profile=False, keep_diagnostics=False,
                 verify=False, **kw):
        self.natives = {}            # image offset -> (name, handler, kind)
        self.native_calls = Counter()
        self.profiling = profile
        self.draw_sites = Counter()  # CS:IP -> bytes written to video memory
        self.native_pixels = 0
        self.warp_calls = 0
        self.rate_mark = (0.0, {}, 0)
        self.frames = 0
        self.buckets = {}
        self.sprite_census = defaultdict(Counter)
        self.active_label = None
        self.mark_t, self.mark_f, self.mark_c = 0.0, 0, {}
        self.mark_px = 0
        self.mark_in = self.mark_out = self.mark_3da = 0
        self.reads = 0
        self.read_bytes = 0
        self.native_time = 0.0
        self.mark_reads = self.mark_rb = 0
        self.mark_nt = 0.0
        self.verify = verify
        self.verify_calls = 0
        self.verify_bad = 0
        self.verify_pending = 0
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

    def set_verify(self, on, reset=True):
        """Turn native-vs-original checking on or off mid-run.

        Verification runs both paths on every call, so it is far too slow to
        leave on; being able to switch it on for a few seconds when something
        looks wrong is what makes it usable.
        """
        if on and reset:
            self.verify_calls = 0
            self.verify_bad = 0
        self.verify = on
        print(f"  [verify] {'ON (counters reset)' if on else 'OFF'} - "
              f"{self.verify_calls} compared, {self.verify_bad} mismatched")
        return on

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

    def _close_bucket(self, now):
        """Fold the open interval into its bucket, if one is open."""
        if self.active_label is None:
            return
        b = self.buckets.setdefault(
            self.active_label, {"t": 0.0, "f": 0, "c": Counter()})
        b["t"] += now - self.mark_t
        b["f"] += self.frames - self.mark_f
        b["px"] = b.get("px", 0) + (self.native_pixels - self.mark_px)
        # Port reads and emulated video writes are the two per-callback costs
        # that can slow the emulator down without the native call count moving.
        b["in"] = b.get("in", 0) + (sum(self.port_in.values()) - self.mark_in)
        b["out"] = b.get("out", 0) + (sum(self.port_out.values()) - self.mark_out)
        b["3da"] = b.get("3da", 0) + (self.port_in.get(0x3DA, 0) - self.mark_3da)
        b["rd"] = b.get("rd", 0) + (self.reads - self.mark_reads)
        b["rb"] = b.get("rb", 0) + (self.read_bytes - self.mark_rb)
        b["nt"] = b.get("nt", 0.0) + (self.native_time - self.mark_nt)
        for k, v in self.native_calls.items():
            b["c"][k] += v - self.mark_c.get(k, 0)

    def mark(self, label):
        """Start or stop sampling into a named bucket.

        Each key toggles its own label: press once to start measuring, again to
        stop. While nothing is active no time is attributed at all, so the gaps
        spent navigating between measurements cannot contaminate either bucket.
        Pressing the other key stops the current one and starts that one.
        """
        now = self._elapsed()
        if self.active_label == label:
            self._close_bucket(now)
            self.active_label = None
            print(f"  [rate] stopped measuring: {label}")
            self.report_buckets()
            return
        self._close_bucket(now)
        self.active_label = label
        self.mark_t, self.mark_f = now, self.frames
        self.mark_c = dict(self.native_calls)
        self.mark_px = self.native_pixels
        self.mark_in = sum(self.port_in.values())
        self.mark_out = sum(self.port_out.values())
        self.mark_3da = self.port_in.get(0x3DA, 0)
        self.mark_reads, self.mark_rb = self.reads, self.read_bytes
        self.mark_nt = self.native_time
        print(f"  [rate] started measuring: {label}")

    def reset_buckets(self):
        self.buckets = {}
        self.sprite_census = defaultdict(Counter)
        self.active_label = None
        print("  [rate] buckets cleared")

    def report_buckets(self):
        if not self.buckets:
            return
        for label, b in self.buckets.items():
            t, f = b["t"], b["f"]
            if t <= 0:
                continue
            calls = sum(b["c"].values())
            px = b.get("px", 0)
            print(f"  [rate] {label}: {t:.1f}s, {f} frames = {f / t:.1f} fps, "
                  f"{px} px = {px / t / 1e6:.2f} Mpx/s, "
                  f"{px / calls if calls else 0:.0f} px/call")
            print(f"           port IN {b.get('in', 0) / t / 1000:8.1f}k/s "
                  f"(0x3da {b.get('3da', 0) / t / 1000:8.1f}k/s), "
                  f"OUT {b.get('out', 0) / t / 1000:.1f}k/s")
            # The decisive split: how much of the wall clock is actually inside
            # the natives, and how many guest-memory round-trips they make.
            nt = b.get("nt", 0.0)
            rd = b.get("rd", 0)
            print(f"           native time {nt:6.2f}s of {t:6.2f}s "
                  f"({100 * nt / t:4.1f}%), "
                  f"guest reads {rd / t / 1000:7.1f}k/s "
                  f"({rd / calls if calls else 0:5.1f}/call, "
                  f"{b.get('rb', 0) / t / 1e6:.2f} MB/s)")
            for name, n in b["c"].most_common():
                if not n:
                    continue
                print(f"           {name:<18} {n / t:9.1f}/s  "
                      f"{(n / f if f else 0):8.1f}/frame")
        # The comparison that actually answers "why is it slower": same work per
        # frame at a lower frame rate is a different problem from more work per
        # frame.
        for label, cen in self.sprite_census.items():
            f = self.buckets.get(label, {}).get("f", 0)
            if not cen or not f:
                continue
            print(f"  [rate] {label}: {len(cen)} distinct sprites, "
                  f"{sum(cen.values()) / f / 4:.1f} sprites/frame (per plane)")
            for (idx, w, h), n in cen.most_common(8):
                print(f"           sprite #{idx:<5} {w:3}x{h:<3} "
                      f"{n / f / 4:7.2f}/frame  ({n} draws)")

        labels = [l for l, b in self.buckets.items() if b["t"] > 0 and b["f"]]
        if len(labels) >= 2:
            a, c = self.buckets[labels[0]], self.buckets[labels[1]]
            fa, fc = a["f"] / a["t"], c["f"] / c["t"]
            print(f"  [rate] {labels[0]} {fa:.1f} fps vs "
                  f"{labels[1]} {fc:.1f} fps")
            for name in set(a["c"]) | set(c["c"]):
                pa = a["c"][name] / a["f"] if a["f"] else 0
                pc = c["c"][name] / c["f"] if c["f"] else 0
                if pa or pc:
                    print(f"           {name:<18} {pa:7.2f} vs {pc:7.2f} "
                          f"calls/frame")

    def report_rates(self):
        """Report calls per second for each native since the last report.

        Cheap enough to leave always available: it just differences the call
        counters against a timestamp, so it can be triggered at the moment
        something feels slow and compared against a moment that feels fine.
        """
        now = self._elapsed()
        prev_t, prev, prev_f = self.rate_mark
        dt = now - prev_t
        df = self.frames - prev_f
        self.rate_mark = (now, dict(self.native_calls), self.frames)
        if dt <= 0:
            return
        rows = []
        for name in sorted(set(self.native_calls) | set(prev)):
            n = self.native_calls.get(name, 0) - prev.get(name, 0)
            if n:
                rows.append((n / dt, n, name))
        rows.sort(reverse=True)
        fps = df / dt
        print(f"  [rate] over {dt:.1f}s: {df} frames = {fps:.1f} fps, "
              f"{sum(r[1] for r in rows)} calls")
        # Report per-frame as well as per-second: a higher call rate can mean
        # either more work per frame or more frames, and only the per-frame
        # figure tells those apart.
        for per_sec, n, name in rows:
            per_frame = n / df if df else 0.0
            print(f"           {name:<18} {per_sec:9.1f}/s  "
                  f"{per_frame:8.1f}/frame  ({n} calls)")
        if not rows:
            print("           no native calls in this interval")

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

        if self.verify and name not in VERIFY_SKIP:
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

        t0 = time.perf_counter()
        result = handler(self, args_at)
        self.native_time += time.perf_counter() - t0

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
        # A code hook added mid-run does not apply to an already-translated
        # block, so without this the return hook usually never fires and the
        # comparison is silently skipped - which made verification look like it
        # covered everything when it was sampling under 1% of calls.
        try:
            uc.ctl_remove_cache(ret_lin, ret_lin + 1)
        except Exception:
            pass
        self.verify_pending += 1
        # Fall through: do NOT skip the body, the real code must run.

    # ------------------------------------------------------------- utilities
    def arg16(self, base, i):
        return struct.unpack("<H", self.uc.mem_read(base + i * 2, 2))[0]

    def arg32(self, base, i):
        return struct.unpack("<I", self.uc.mem_read(base + i * 2, 4))[0]

    def read(self, addr, n):
        # Counted so guest-memory round-trips can be compared against pixel
        # work: each one is a ctypes call into Unicorn, and a routine that
        # reads per row pays it hundreds of times per call.
        self.reads += 1
        self.read_bytes += n
        return bytes(self.uc.mem_read(addr, n))

    def write(self, addr, data):
        self.uc.mem_write(addr, bytes(data))


def read_row_table(m, table, n):
    """Resolve n far row pointers with ONE guest read instead of n.

    Measurement showed ~417 uc.mem_read calls per native call, four per row,
    accounting for essentially all the time spent in the natives. Reading the
    table in one go removes half of them.
    """
    raw = m.read(table, n * 4)
    vals = struct.unpack_from(f"<{n * 2}H", raw)
    return [vals[i * 2 + 1] * 16 + vals[i * 2] for i in range(n)]


def bulk_rows(m, rows, start, span):
    """Fetch `span` bytes from each row, in one read when rows are contiguous.

    Sprite and tile data is normally one image buffer with a constant row
    stride, so the whole region can come back in a single read; fall back to
    per-row reads when it is not.
    """
    n = len(rows)
    if n == 0:
        return []
    if n > 1:
        stride = rows[1] - rows[0]
        if stride > 0 and all(rows[i + 1] - rows[i] == stride
                              for i in range(n - 1)) and stride >= span:
            blob = m.read(rows[0] + start, (n - 1) * stride + span)
            return [blob[i * stride:i * stride + span] for i in range(n)]
    return [m.read(r + start, span) for r in rows]


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

    # Resolve both row tables and fetch all source rows up front: the guest
    # memory round-trips, not the pixel arithmetic, are what cost the time.
    span = max(0, width - x0)
    fg_rows = read_row_table(m, fg_table, height)
    bg_rows = read_row_table(m, bg_table, mask_y + 1)
    fg_data = bulk_rows(m, fg_rows, x0, span)
    bg_cache = {}
    idx = (np.arange(x0, width, 4, dtype=np.int32) & mask_x)

    out = bytearray()
    for row in range(height):
        by = (row + y0) & mask_y
        bg = bg_cache.get(by)
        if bg is None:
            bg = np.frombuffer(m.read(bg_rows[by], mask_x + 1), dtype=np.uint8)
            bg_cache[by] = bg
        fg = np.frombuffer(fg_data[row], dtype=np.uint8)[::4]
        out += np.where(fg == 0, bg[idx[:len(fg)]], fg).tobytes()

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

    # While a rate bucket is open, record what is being drawn. Knowing the
    # count is not enough to explain a redraw storm; the sprite identity and
    # size say whether it is one big image, a row of letters, or a whole scene.
    if m.active_label is not None:
        m.sprite_census[m.active_label][(index, w, h)] += 1

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
    buf = np.frombuffer(data, dtype=np.uint8)
    planes = [m.planes[p] for p in m.active_planes]
    # Only columns where x & 3 == plane are written, so step the row by 4 from
    # the first such column instead of testing every pixel.
    first = x + ((plane - x) & 3)
    drawn = 0
    for row in range(y, y_end):
        rowbase = plane_off + base + row * stride
        lo = src + (first - x)
        sel = buf[lo:src + (x_end - x):4]
        if not len(sel):
            src += (x_end - x) + row_extra
            continue
        nz = sel != 0
        if nz.any():
            vals = ((sel[nz].astype(np.uint16) + colour) & 0xFF).astype(np.uint8)
            offs = rowbase + ((first + np.nonzero(nz)[0] * 4) >> 2)
            for pl in planes:
                for o, v in zip(offs.tolist(), vals.tolist()):
                    if 0 <= o < len(pl):
                        pl[o] = v
            drawn += int(nz.sum())
        src += (x_end - x) + row_extra
    m.native_pixels += drawn
    return None


def native_clear_vram(m, args):
    """Native replacement for the full-screen clear at 0x04d2a. Takes no args.

    The original sets the sequencer map mask to all four planes and then writes
    zero to 64000 consecutive offsets - 256000 pixels, one emulated iteration
    each, re-loading the far pointer every time.

    Two details matter for equivalence:
      * it programs the map mask itself via OUT 0x3c4, 0xff02, and we skip that
        instruction, so the native must apply the same sequencer write or later
        drawing inherits a stale mask;
      * the destination offset is 16-bit, so the run wraps at 0x10000 rather
        than spilling past the end of the plane.
    """
    g = m.dgroup_base
    off, seg = struct.unpack("<HH", m.read(g + 0x16F1, 4))
    m._seq_write(0x02, 0xFF)          # what the OUT we skipped would have done

    start = (seg * 16 + off) - 0xA0000
    if start < 0:
        return None
    n = 0xFA00
    for p in range(4):
        pl = m.planes[p]
        base, size = start & 0xFFFF, len(pl)
        end = base + n
        if end <= size:
            pl[base:end] = bytes(n)
        else:                          # wrap within the 64 KB plane
            first = size - base
            pl[base:size] = bytes(first)
            pl[0:n - first] = bytes(n - first)
    m.native_pixels += n * 4
    return None


def native_plot_pixel(m, args):
    """Native replacement for the single-pixel plot at 0x05761.

        [+0x06] word : x      [+0x08] word : y      [+0x0a] byte : colour

    Writes nothing unless x & 3 equals the current plane, so the game calls it
    up to four times per pixel.

    Note this routine computes its row stride as 80 unconditionally, with no
    [0x4fe] resolution check - unlike every other drawing routine here. That
    looks like an oversight in the original for 360-wide mode, but the native
    reproduces it rather than silently correcting it: the point is to be
    identical, and --verify would flag any "improvement" as a mismatch.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    x, y = u16(args + 0), u16(args + 2)
    if m.read(g + 0x177D, 1)[0] != (x & 3):
        return None
    off, seg = struct.unpack("<HH", m.read(g + 0x16F1, 4))
    o = seg * 16 + off - 0xA0000 + y * 80 + (x >> 2) + u16(g + 0x1727)
    colour = m.read(args + 4, 1)[0]
    for p in m.active_planes:
        if 0 <= o < len(m.planes[p]):
            m.planes[p][o] = colour
    m.native_pixels += 1
    return None


def native_blit_rows_masked(m, args):
    """Native replacement for the masked row blitter at 0x05ac2.

    Same arguments and layout as blit_rows (0x05c09), but source bytes of zero
    are transparent and leave the destination untouched. Read the existing row,
    overlay the non-zero source pixels, write it back in one go.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    table = far(far(args + 0x00))
    row0, row1 = u16(args + 0x04), u16(args + 0x06)
    x0, x1 = u16(args + 0x08), u16(args + 0x0A)
    srcrow = u16(args + 0x18)

    plane = m.read(g + 0x177D, 1)[0]
    stride = 90 if u16(g + 0x4FE) else 80
    base = far(g + 0x16F1) - 0xA0000 + u16(g + 0x1727)
    if base < 0 or not m.active_planes:
        return None

    sx = x0 + plane
    n = max(0, (x1 - sx + 3) // 4)
    if n == 0:
        return None
    planes = [m.planes[p] for p in m.active_planes]
    for row in range(row0, row1):
        src = np.frombuffer(m.read(far(table + srcrow * 4) + plane, n * 4),
                            dtype=np.uint8)[::4]
        o = base + row * stride + (sx >> 2)
        nz = src != 0
        for pl in planes:
            if o < 0 or o + n > len(pl):
                continue
            cur = np.frombuffer(bytes(pl[o:o + n]), dtype=np.uint8).copy()
            cur[nz] = src[nz]
            pl[o:o + n] = cur.tobytes()
        m.native_pixels += int(nz.sum())
        srcrow += 1
    return None


def native_compose_scroll(m, args):
    """Native replacement for the scrolling compositor at 0x05dc4.

        [+0x06] word : x scroll
        [+0x08] word : y scroll

    Like compose_layer, but scrolled and with the optional background warp the
    changelog describes: when [0x2022] is set, each row's x displacement comes
    from a 32-entry table at [0x179f], stepped by [0x17c0] per row. Foreground
    pixel wins unless it is zero, in which case the wrapped background shows
    through. Column steps 4, so one call fills a single Mode X plane.
    """
    g = m.dgroup_base
    u8 = lambda a: m.read(a, 1)[0]
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    argx, argy = u16(args + 0), u16(args + 2)
    base_x = (argx >> 1) + u8(g + 0x177E)
    row_adv = (s16(g + 0x538) - s16(g + 0x1735)) >> 2
    row0, row_end = u16(g + 0x172D), u16(g + 0x172F)
    right = u16(g + 0x1735)
    mask_x, mask_y = u16(g + 0x1729), u16(g + 0x172B)
    plane = u8(g + 0x177D)
    warp_on = u16(g + 0x2022) != 0
    phase = u8(g + 0x17BF)
    step = u8(g + 0x17C0)
    if warp_on:
        phase = (phase + ((argy >> 1) * step)) & 0xFF
        m.warp_calls += 1        # so we can tell whether this path was tested

    stride = 90 if u16(g + 0x4FE) else 80
    dst = (far(g + 0x16F1) - 0xA0000 + row0 * stride
           + u16(g + 0x1727) + (s16(g + 0x1731) >> 2))
    if dst < 0 or not m.active_planes:
        return None

    fg_table, bg_table = far(g + 0x16F5), far(g + 0x170B)
    planes = [m.planes[p] for p in m.active_planes]
    span = max(0, (right - plane + 3) // 4)
    y0 = u8(g + 0x177F)
    shift = base_x
    di, cur = argy, row0

    # One read each for the row tables, and cache background rows: the wrap
    # mask means the same few rows recur, and each re-read was a ctypes call.
    nrows = max(0, row_end - row0)
    fg_rows = read_row_table(m, fg_table, di + nrows) if nrows else []
    bg_rows = read_row_table(m, bg_table, mask_y + 1)
    fg_data = bulk_rows(m, fg_rows[di:di + nrows], argx + plane, span * 4)
    bg_cache = {}
    cols = np.arange(span, dtype=np.int32) * 4 + plane

    while cur < row_end:
        by = (((argy >> 1) + y0 + cur) & mask_y)
        if warp_on:
            phase &= 0x1F
            shift = base_x + u8(g + 0x179F + phase)
            phase = (phase + step) & 0xFF
        bg = bg_cache.get(by)
        if bg is None:
            bg = np.frombuffer(m.read(bg_rows[by], mask_x + 1), dtype=np.uint8)
            bg_cache[by] = bg
        fg = np.frombuffer(fg_data[cur - row0], dtype=np.uint8)[::4]
        idx = (cols[:len(fg)] + shift) & mask_x
        out = np.where(fg == 0, bg[idx], fg).tobytes()
        for pl in planes:
            if 0 <= dst and dst + len(out) <= len(pl):
                pl[dst:dst + len(out)] = out
        m.native_pixels += len(out)
        dst += len(out) + row_adv
        di += 1
        cur += 1
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
    (0x05DC4, "compose_scroll", native_compose_scroll, "far"),
    (0x05AC2, "blit_rows_masked", native_blit_rows_masked, "far"),
    (0x05761, "plot_pixel", native_plot_pixel, "far"),
    (0x04D2A, "clear_vram", native_clear_vram, "far"),
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
    ap.add_argument("--sound-slices", type=int, default=32,
                    help="times per display update to service the sound card")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--wav", default="ducks_native.wav")
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
    audio = None
    if args.blaster and not args.no_audio:
        audio = AudioSink()

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
        # Run the chunk in slices, servicing the sound card between each. One
        # service per chunk leaves the sound IRQ hundreds of thousands of
        # instructions late, and the game then refills its DMA buffer too
        # slowly to produce continuous audio.
        slices = max(1, args.sound_slices if m.sb is not None else 1)
        step = max(1000, args.chunk // slices)
        for _ in range(slices):
            try:
                m.uc.emu_start(addr, 0, count=step)
            except UcError as e:
                print(f"  [cpu] {e} at {m._reg(UC_X86_REG_CS):04x}:"
                      f"{m._reg(UC_X86_REG_IP):04x}")
                running = False
                break
            if m.finished:
                print(f"  [dos] program exited: {m.finished}")
                running = False
                break
            addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
            m.service_sound()
            addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
        if audio is not None:
            audio.push(m.sb)

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
                elif ev.key == pygame.K_F7:
                    m.set_verify(not m.verify)
                elif ev.key == pygame.K_F8:
                    m.mark("hover")
                elif ev.key == pygame.K_F9:
                    m.mark("no-hover")
                elif ev.key == pygame.K_F4:
                    m.reset_buckets()
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
                             ("trace.report", "report"),
                             ("verify.on", "von"), ("verify.off", "voff"),
                             ("rate.report", "rate")):
            if os.path.exists(name):
                os.remove(name)
                if action == "on":
                    m.enable_profiling()
                    print("  [trace] ON (counters reset)")
                elif action == "off":
                    m.disable_profiling()
                    print("  [trace] OFF")
                elif action == "von":
                    m.set_verify(True)
                elif action == "voff":
                    m.set_verify(False)
                elif action == "rate":
                    m.report_rates()
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
        m.frames = frames
        clock.tick(60)

        if m._elapsed() >= next_status:
            next_status += args.status_every
            print(f"  [stat] t={m._elapsed():6.1f}s frames={frames} "
                  f"mode={m.mode:#04x} natives={dict(m.native_calls)}")

    print(f"\n=== finished after {frames} frames, {m._elapsed():.1f}s ===")
    if m.native_calls:
        print(f"  native calls    : {dict(m.native_calls)}")
        print(f"  pixels drawn natively: {m.native_pixels}")
        print(f"  background-warp path : {m.warp_calls} calls"
              + ("" if m.warp_calls else "  <-- NOT exercised, so unverified"))
    if m.verify:
        att = m.verify_pending
        pct = 100.0 * m.verify_calls / att if att else 0.0
        print(f"  verify          : {m.verify_calls} of {att} attempted "
              f"({pct:.0f}% actually compared), {m.verify_bad} MISMATCHED")
        if m.verify_calls < att:
            print(f"                    {att - m.verify_calls} comparisons "
                  f"never completed - this is a sample, not full coverage")
        never = [n for _, n, _, _ in NATIVE_TABLE
                 if n not in m.native_calls and n not in VERIFY_SKIP]
        if never:
            print(f"                    never called, so unverified: "
                  f"{', '.join(never)}")
    if audio is not None:
        print(f"  audio streaming : {audio.queued} chunks queued, "
              f"{audio.dropped} dropped")
    if m.sb is not None:
        print(f"  audio written   : {m.sb.write_wav(args.wav) or 'no PCM'}")
    if m.draw_sites:
        m.profile_report(img)
    pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
