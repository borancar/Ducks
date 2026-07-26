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
from trace_dos import GAME_DIR, host_path, DOS_FN
from nsound import NativeVoices, SoundBank

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
                 verify=False, native_sound=False, native_mouse=False,
                 native_keyboard=False, native_file=False, native_xms=False,
                 persist=True, **kw):
        self.native_sound = native_sound
        self.native_mouse = native_mouse
        self.native_keyboard = native_keyboard
        self.native_file = native_file
        self.native_xms = native_xms
        self.persist = persist
        self.files_persisted = {}
        self.image_base = 0          # real value after super().__init__
        self.int_sites = Counter()   # (intno, ah, linear site) -> count
        self.voices = None
        self.file_reads = self.file_seeks = self.file_bytes = 0
        self.native_declined = 0
        self.egg_access = []
        self.bank = SoundBank()
        self.trace_mouse = False
        self.mouse_stacks = Counter()
        self.trace_keyboard = False
        self.kbd_stacks = Counter()
        self.trace_file = False
        self.file_stacks = Counter()
        self.file_io = Counter()
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
        self._cache = {}
        self._cache_pages = {}
        self._cache_hooks = {}
        self.cache_hits = self.cache_misses = self.cache_drops = 0
        self.sound_sites = Counter()
        self._sound_hook = None
        self._want_sound_profile = False
        self.xms_callers = Counter()
        self.xms_sizes = defaultdict(list)
        self.call_tracers = defaultdict(Counter)
        self.call_args = defaultdict(Counter)
        self.writer_sites = Counter()
        self.writer_fields = Counter()
        self.play_calls = 0
        self.play_samples = Counter()
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
        if getattr(self, "_want_sound_profile", False):
            self.profile_sound()
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
        tot = self.cache_hits + self.cache_misses
        if tot:
            print(f"  [rate] source cache: {100 * self.cache_hits / tot:.1f}% "
                  f"hit ({self.cache_hits} hits, {self.cache_misses} misses, "
                  f"{self.cache_drops} invalidations, "
                  f"{len(self._cache)} entries, "
                  f"{len(self._cache_hooks)} watched pages)")
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

    def _dos(self):
        """Service DOS calls, optionally recording who polls the keyboard.

        The game never uses INT 16h - it reaches the keyboard through DOS
        check-stdin and read-char, i.e. Borland's kbhit()/getch(). Same BP-chain
        trick as the mouse, since these are library calls too.

        Also the point where writes become real. The tracer deliberately keeps
        the host filesystem read-only, so saves lived in its overlay and died
        with the process. Here a close flushes to the game directory instead,
        which is what makes a save survive a restart.
        """
        ah = (self._reg(UC_X86_REG_AX) >> 8) & 0xFF
        if self.trace_keyboard and ah in (0x01, 0x06, 0x07, 0x08, 0x0B):
            self.kbd_stacks[(ah,) + self._bp_chain(5)] += 1
        if self.trace_file and ah in (0x3C, 0x3D, 0x3E, 0x3F, 0x40, 0x42):
            self.file_stacks[(ah,) + self._bp_chain(6)] += 1
            if ah in (0x3F, 0x40):
                self.file_io[ah] += self._reg(UC_X86_REG_CX)
        # Capture what the call is about to consume: the parent pops the handle
        # on close and drops the overlay entry on delete, so both are gone by
        # the time it returns.
        closing = deleting = None
        if self.persist and ah == 0x3E:
            h = self.handles.get(self._reg(UC_X86_REG_BX))
            if h is not None and getattr(h, "key", None):
                closing = h
        elif self.persist and ah == 0x41:
            deleting = self._str(self._reg(UC_X86_REG_DS),
                                 self._reg(UC_X86_REG_DX))
        r = super()._dos()
        if closing is not None:
            self._persist(closing.path, bytes(closing.data))
        if deleting is not None:
            self._unpersist(deleting)
        return r

    def _writable_host_path(self, name):
        """Resolve a DOS path for writing, or None if it is out of bounds.

        Two guards. Writes must land inside GAME_DIR - the game only ever names
        bare filenames, so anything escaping it means we misread the path. And
        the game has no business rewriting its own code or data: per the
        analysis plan's negative checks, a write to an .exe or .egg is a finding,
        not something to quietly perform.
        """
        hp = os.path.abspath(host_path(name))
        root = os.path.abspath(GAME_DIR)
        if os.path.commonpath([hp, root]) != root:
            self._fop(f"REFUSED write outside game dir: {name!r} -> {hp}")
            return None
        if os.path.splitext(hp)[1].lower() in (".exe", ".egg", ".com"):
            self._fop(f"REFUSED write to program/data file: {name!r}")
            return None
        return hp

    def _persist(self, name, data):
        """Write a closed file out for real, atomically."""
        hp = self._writable_host_path(name)
        if hp is None:
            return
        tmp = hp + ".part"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, hp)
        except OSError as e:
            self._fop(f"SAVE FAILED {name!r}: {e}")
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return
        self.files_persisted[name] = len(data)
        self._fop(f"SAVED {name!r} -> {hp} ({len(data)} bytes)")

    def _unpersist(self, name):
        hp = self._writable_host_path(name)
        if hp is None or not os.path.isfile(hp):
            return
        try:
            os.unlink(hp)
            self._fop(f"DELETED {name!r} -> {hp}")
            self.files_persisted.pop(name, None)
        except OSError as e:
            self._fop(f"DELETE FAILED {name!r}: {e}")

    def flush_open_files(self):
        """Write out anything still open, for a quit mid-write.

        The game closes its saves properly, so this only matters when the window
        is closed at the wrong moment - but losing a save to that would be a
        confusing way to find out.
        """
        if not self.persist:
            return
        for h in list(self.handles.values()):
            if getattr(h, "key", None) and h.written:
                self._persist(h.path, bytes(h.data))

    # Interrupts carrying a meaningful function number in AH; for the rest the
    # number alone identifies the service.
    _INT_HAS_AH = (0x21, 0x2F, 0x10, 0x16, 0x33, 0x13, 0x15)

    def _on_intr(self, uc, intno, user):
        """Record where each interrupt is raised, then service it normally.

        Counts alone cannot drive removal - the goal is to replace the code that
        raises the interrupt, so we need its address. IP points just past the
        two-byte INT when the hook fires.
        """
        site = (self._reg(UC_X86_REG_CS) * 16 +
                ((self._reg(UC_X86_REG_IP) - 2) & 0xFFFF))
        ah = (self._reg(UC_X86_REG_AX) >> 8) & 0xFF
        self.int_sites[(intno, ah if intno in self._INT_HAS_AH else None,
                        site)] += 1
        return super()._on_intr(uc, intno, user)

    def int_report(self, img):
        """Remaining interrupts, grouped by service and attributed to a caller.

        The inventory of what is left to replace: anything listed here is still
        going through emulated hardware or an emulated DOS.
        """
        if not self.int_sites:
            return
        print("\n=== interrupts still executed (site -> enclosing function) ===")
        by_svc = defaultdict(list)
        for (intno, ah, site), n in self.int_sites.items():
            by_svc[(intno, ah)].append((n, site))
        for (intno, ah), sites in sorted(
                by_svc.items(), key=lambda kv: -sum(n for n, _ in kv[1])):
            total = sum(n for n, _ in sites)
            svc = f"INT {intno:02x}h" + (f" AH={ah:02x}h" if ah is not None else "")
            desc = DOS_FN.get(ah, "") if intno == 0x21 else ""
            print(f"  {svc:<16} x{total:<7} {desc}")
            for n, site in sorted(sites, key=lambda s: -s[0])[:4]:
                off = site - self.image_base
                if 0 <= off < len(img):
                    f = find_function_start(img, off)
                    where = f"{off:#07x}" + (f" in {f:#07x}" if f is not None
                                             else " (no prologue found)")
                else:
                    where = f"outside image (linear {site:#07x})"
                print(f"       x{n:<7} at {where}")

    def file_report(self, img):
        if not self.file_stacks:
            return
        FN = {0x3C: "create", 0x3D: "open", 0x3E: "close", 0x3F: "read",
              0x40: "write", 0x42: "lseek"}
        print("\n=== file I/O callers (DOS fn, then BP chain) ===")
        for chain, n in self.file_stacks.most_common(12):
            ah, frames = chain[0], chain[1:]
            named = []
            for off in frames:
                f = find_function_start(img, off)
                named.append(f"{f:#07x}" if f is not None else f"?{off:#07x}")
            print(f"  AH={ah:02x}h ({FN.get(ah, '?'):6}) x{n:<5} "
                  f"{' <- '.join(named)}")
        print(f"  bytes read {self.file_io.get(0x3F, 0)}, "
              f"written {self.file_io.get(0x40, 0)}")

    def kbd_report(self, img):
        if not self.kbd_stacks:
            return
        print("\n=== keyboard pollers (DOS fn, then BP chain) ===")
        for chain, n in self.kbd_stacks.most_common(10):
            ah, frames = chain[0], chain[1:]
            named = []
            for off in frames:
                f = find_function_start(img, off)
                named.append(f"{f:#07x}" if f is not None else f"?{off:#07x}")
            print(f"  AH={ah:02x}h x{n:<7} {' <- '.join(named)}")

    def _mouse(self):
        """Service INT 33h, optionally recording who asked.

        The interrupt number is patched into the instruction at runtime by
        Borland's int86, so there is no static `CD 33` to search for and the
        caller cannot be found by disassembly. Walk the BP chain instead:
        Borland sets up standard frames, so [BP+2]/[BP+4] is a return address
        and [BP] links to the caller's frame. A few levels of that reaches the
        game's own mouse code, above the library shim.
        """
        if self.trace_mouse:
            self.mouse_stacks[self._bp_chain(5)] += 1
        return super()._mouse()

    def _bp_chain(self, depth):
        frames = []
        bp = self._reg(UC_X86_REG_BP)
        ss = self._reg(UC_X86_REG_SS)
        for _ in range(depth):
            if not 2 <= bp < 0xFFF0:
                break
            try:
                nxt, ip, cs = struct.unpack(
                    "<HHH", self.uc.mem_read(ss * 16 + bp, 6))
            except Exception:
                break
            off = cs * 16 + ip - self.image_base
            if not 0 <= off < 0x20000:
                break
            frames.append(off)
            if nxt <= bp:
                break
            bp = nxt
        return tuple(frames)

    def mouse_report(self, img):
        if not self.mouse_stacks:
            return
        print("\n=== INT 33h callers (BP chain, innermost first) ===")
        for chain, n in self.mouse_stacks.most_common(8):
            named = []
            for off in chain:
                f = find_function_start(img, off)
                named.append(f"{f:#07x}" if f is not None else f"?{off:#07x}")
            print(f"  x{n:<7} {' <- '.join(named)}")

    def capture_loader(self, off=0x14F07):
        """Capture each sample into the bank as the loader finishes building it.

        Hooked on RETURN, not entry: on entry the descriptor does not exist yet.
        The loader mallocs a 10-byte descriptor, writes handle/start/length into
        it, streams the sample from the egg in 2 KB chunks scaling each byte by
        di/32, and XMS-moves it in. So after it returns, the descriptor is
        complete and the bytes are in XMS - which is the moment to copy them out
        under a stable index.

        Observational only: the loader still runs. Reimplementing it would mean
        reproducing its stdio stream reads, for something that happens once per
        level.
        """
        lin = self.image_base + off
        self.uc.hook_add(UC_HOOK_CODE, self._on_loader, None, lin, lin)
        print(f"  [bank] capturing samples from the loader at {off:#07x}")

    def _on_loader(self, uc, address, size, user):
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
        try:
            ip, cs = struct.unpack("<HH", uc.mem_read(ss * 16 + sp, 4))
            out_off, out_seg = struct.unpack("<HH",
                                            uc.mem_read(ss * 16 + sp + 4, 4))
            scale = struct.unpack("<H", uc.mem_read(ss * 16 + sp + 14, 2))[0]
        except Exception:
            return
        ret_lin = cs * 16 + ip
        state = {"h": None}

        def on_return(uc2, a2, s2, u2):
            if a2 != ret_lin:
                return
            uc2.hook_del(state["h"])
            try:
                d_off, d_seg = struct.unpack(
                    "<HH", uc2.mem_read(out_seg * 16 + out_off, 4))
                raw = bytes(uc2.mem_read(d_seg * 16 + d_off, 10))
            except Exception:
                return
            handle = struct.unpack_from("<H", raw, 0)[0]
            start = struct.unpack_from("<I", raw, 2)[0]
            length = struct.unpack_from("<I", raw, 6)[0]
            blk = self.xms.handles.get(handle)
            if blk is None or not length or start + length > len(blk):
                print(f"  [bank] skipped: handle {handle} start {start} "
                      f"len {length} not resident")
                return
            pcm = (np.frombuffer(bytes(blk[start:start + length]),
                                 dtype=np.uint8) ^ 0x80).tobytes()
            self.bank.add(handle, start, length, scale, pcm)

        state["h"] = uc.hook_add(UC_HOOK_CODE, on_return, None,
                                 ret_lin, ret_lin)
        try:
            uc.ctl_remove_cache(ret_lin, ret_lin + 1)
        except Exception:
            pass

    def observe_play_sample(self, off=0x151D2):
        """Observe play_sample and decode the sample descriptor it is handed.

        The layout was derived by matching what 0x155be reads against the XMS
        move structure it builds, which implies:

            +0x00 word   XMS handle
            +0x02 dword  start offset within that handle's block
            +0x06 dword  length in bytes

        Rather than trust that, check it: the handle should be one we actually
        allocated, and start+length should fit inside it. A layout error would
        show up immediately as a bogus handle or an out-of-range extent.
        """
        lin = self.image_base + off
        self.uc.hook_add(UC_HOOK_CODE, self._on_play_sample, None, lin, lin)
        print(f"  [play] observing play_sample at {off:#07x}")

    def _on_play_sample(self, uc, address, size, user):
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
        try:
            a = struct.unpack("<4H", uc.mem_read(ss * 16 + sp + 4, 8))
        except Exception:
            return
        desc_off, desc_seg, param, volume = a
        try:
            raw = bytes(uc.mem_read(desc_seg * 16 + desc_off, 10))
        except Exception:
            return
        handle = struct.unpack_from("<H", raw, 0)[0]
        start = struct.unpack_from("<I", raw, 2)[0]
        length = struct.unpack_from("<I", raw, 6)[0]

        blk = self.xms.handles.get(handle)
        if blk is None:
            verdict = f"handle {handle} NOT ALLOCATED"
        elif start + length > len(blk):
            verdict = (f"extent {start}+{length} exceeds handle {handle} "
                       f"({len(blk)} bytes)")
        else:
            verdict = f"fits handle {handle} ({len(blk)} bytes) - layout OK"
        self.play_calls += 1
        self.play_samples[(handle, start, length)] += 1
        if self.play_calls <= 12:
            print(f"  [play] desc {desc_seg:04x}:{desc_off:04x} "
                  f"handle={handle} start={start} len={length} "
                  f"param={param:#06x} vol={volume:#06x} raw={raw.hex()} "
                  f"-> {verdict}")

    def play_report(self):
        if not self.play_calls:
            return
        print(f"\n=== play_sample: {self.play_calls} calls, "
              f"{len(self.play_samples)} distinct samples ===")
        for (h, s, n), c in self.play_samples.most_common(12):
            blk = self.xms.handles.get(h)
            ok = "ok" if blk is not None and s + n <= len(blk) else "BAD"
            print(f"  handle {h} start {s:>7} len {n:>7}  x{c:<4} {ok}")

    def watch_writers(self, lo, hi):
        """Count which functions write to a DGROUP range.

        Finding the routine that starts a sound is easier from the data side
        than by climbing the call graph: whatever populates a voice slot is the
        trigger, whichever layer it happens to sit in.
        """
        base = self.dgroup_base
        a, b = base + lo, base + hi

        def on_write(uc, access, address, size, value, user):
            off = address - base
            self.writer_sites[(uc.reg_read(UC_X86_REG_CS),
                               uc.reg_read(UC_X86_REG_IP))] += 1
            self.writer_fields[off] += 1

        self.uc.hook_add(UC_HOOK_MEM_WRITE, on_write, None, a, b)
        print(f"  [watch] counting writers of DGROUP {lo:#06x}..{hi:#06x}")

    def writer_report(self, img):
        if not self.writer_sites:
            return
        print("\n=== writers of the watched DGROUP range ===")
        by_func = defaultdict(int)
        detail = defaultdict(list)
        for (cs, ip), n in self.writer_sites.most_common(60):
            off = cs * 16 + ip - self.image_base
            fn = find_function_start(img, off)
            by_func[fn] += n
            detail[fn].append((off, n))
        for fn, n in sorted(by_func.items(), key=lambda kv: -kv[1]):
            name = f"{fn:#07x}" if fn is not None else "  unknown"
            sites = ", ".join(f"{o:#07x}({c})" for o, c in detail[fn][:5])
            print(f"  {name:>10}  {n:>7} writes   {sites}")
        print("  fields touched: " + ", ".join(
            f"{o:#06x}:{c}" for o, c in
            sorted(self.writer_fields.items())[:16]))

    def add_call_tracers(self, offsets):
        """Observe calls to given functions and record who called them.

        Walking the call graph upward is how the real entry point is found: the
        mixer machinery runs at a steady rate no matter what happens, while the
        function that actually starts a sound fires sporadically, in step with
        game events. Comparing call rates separates the two.

        These hooks only observe - the original body still runs.
        """
        for off in offsets:
            lin = self.image_base + off
            self.uc.hook_add(UC_HOOK_CODE, self._on_traced_call,
                             None, lin, lin)
            print(f"  [call] tracing calls to {off:#07x}")

    def _on_traced_call(self, uc, address, size, user):
        off = address - self.image_base
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
        try:
            ip, cs = struct.unpack("<HH", uc.mem_read(ss * 16 + sp, 4))
            caller = cs * 16 + ip - self.image_base
        except Exception:
            caller = None
        self.call_tracers[off][caller] += 1
        # Capture a few argument words too: the trigger's arguments should
        # include something identifying which sound to play.
        try:
            args = struct.unpack("<4H", uc.mem_read(ss * 16 + sp + 4, 8))
            self.call_args[(off, caller)][args] += 1
        except Exception:
            pass

    def call_report(self, img):
        if not self.call_tracers:
            return
        el = max(self._elapsed(), 1e-6)
        print("\n=== traced calls (walking up towards the trigger) ===")
        for off, callers in self.call_tracers.items():
            total = sum(callers.values())
            print(f"  {off:#07x}: {total} calls, {total / el:.1f}/s")
            for caller, n in callers.most_common(6):
                fn = find_function_start(img, caller) \
                    if caller is not None else None
                where = f"{fn:#07x}" if fn is not None else "unknown"
                at = f"{caller:#07x}" if caller is not None else "?"
                print(f"      from {where} (call at {at}): {n} "
                      f"({n / el:.1f}/s)")
                common = self.call_args.get((off, caller), Counter())
                for args, c in common.most_common(2):
                    print(f"          args {[hex(a) for a in args]} x{c}")

    def _xms_call(self):
        """Record who is calling XMS before servicing it.

        The game reaches XMS through thin wrappers (push bp; mov bp,sp;
        mov ah,fn; lcall [0x2b46]), so at the point the stub traps, BP still
        points at the wrapper's frame: [BP+2]/[BP+4] is the return address of
        whatever called the wrapper. That is the function actually loading or
        moving sample data, which is what we want to identify.
        """
        ah = (self._reg(UC_X86_REG_AX) >> 8) & 0xFF
        try:
            ss, bp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_BP)
            off, seg = struct.unpack("<HH", self.uc.mem_read(ss * 16 + bp + 2, 4))
            caller = seg * 16 + off - self.image_base
        except Exception:
            caller = None
        size = self._reg(UC_X86_REG_DX) if ah == 0x09 else (
            self._reg(UC_X86_REG_BX) if ah == 0x0F else None)
        self.xms_callers[(ah, caller)] += 1
        if size is not None:
            self.xms_sizes[(ah, caller)].append(size)
        return super()._xms_call()

    def install_native_xms(self):
        """Service XMS at its entry point directly, with no interrupt at all.

        The driver entry the game far-calls is our own three-byte stub,
        INT 60h; RETF - so every XMS request cost an interrupt purely because of
        how the stub was built, 194 of them in a two-level session. The API
        behind the vector was already pure Python, so hooking the entry address
        itself removes the interrupts without changing a single semantic: the
        same _xms_call() services the same registers, and we perform the far
        return the stub's RETF would have done.
        """
        lin = emulation.XMS_STUB_SEG * 16
        self.uc.hook_add(UC_HOOK_CODE, self._on_xms_entry, None, lin, lin)
        try:
            self.uc.ctl_remove_cache(lin, lin + 3)
        except Exception:
            pass
        print(f"  [xms] entry serviced natively at "
              f"{emulation.XMS_STUB_SEG:04x}:0000 - no INT "
              f"{emulation.XMS_INT:02x}h")

    def _on_xms_entry(self, uc, address, size, user):
        self._xms_call()
        ss, sp = self._reg(UC_X86_REG_SS), self._reg(UC_X86_REG_SP)
        ip, cs = struct.unpack("<HH", uc.mem_read(ss * 16 + sp, 4))
        self._set(UC_X86_REG_SP, (sp + 4) & 0xFFFF)
        uc.reg_write(UC_X86_REG_CS, cs)
        uc.reg_write(UC_X86_REG_IP, ip)
        self.native_calls["xms_entry"] += 1

    def xms_report(self, img):
        if not self.xms_callers:
            print("  [xms] no XMS calls recorded")
            return
        print("\n=== XMS callers (sample loading) ===")
        FN = {0x00: "version", 0x08: "query free", 0x09: "allocate",
              0x0A: "free", 0x0B: "move", 0x0F: "realloc"}
        for (ah, caller), n in self.xms_callers.most_common(20):
            fn = find_function_start(img, caller) if caller is not None else None
            where = f"{fn:#07x}" if fn is not None else "unknown"
            at = f"{caller:#07x}" if caller is not None else "?"
            sizes = self.xms_sizes.get((ah, caller), [])
            extra = ""
            if sizes:
                extra = (f"  sizes {min(sizes)}..{max(sizes)} KB, "
                         f"last {sizes[-1]} KB")
            print(f"  AH={ah:02x}h ({FN.get(ah, '?'):<10}) x{n:<5} "
                  f"from {where} (call at {at}){extra}")

    def profile_sound(self):
        """Attribute writes to the sound DMA buffer to the code that made them.

        The same discovery step that found the drawing routines: we cannot
        replace the mixer without knowing which function it is. The buffer
        address is only known once the game programmes the DMA controller, so
        this arms itself when that has happened.
        """
        sb = self.sb
        if self._sound_hook is not None:
            return False
        # Remember the request: the DMA buffer address is not known until the
        # game programmes the controller, which happens after the sound check,
        # so arming has to be able to wait rather than silently doing nothing.
        if sb is None or not sb.dma_active:
            if not self._want_sound_profile:      # announce once, not per slice
                print("  [snd] sound profiling requested; will arm once the "
                      "game starts DMA playback")
            self._want_sound_profile = True
            return False
        self._want_sound_profile = True
        lo = (sb.dma_page << 16) | sb.dma_addr
        hi = lo + max(512, getattr(sb, "dma_len", 512)) - 1

        def on_write(uc, access, address, size, value, user):
            self.sound_sites[(uc.reg_read(UC_X86_REG_CS),
                              uc.reg_read(UC_X86_REG_IP))] += size

        self._sound_hook = self.uc.hook_add(UC_HOOK_MEM_WRITE, on_write,
                                           None, lo, hi)
        print(f"  [snd] profiling writes to the DMA buffer "
              f"{lo:#07x}..{hi:#07x}")
        return True

    def sound_report(self, img):
        if not self.sound_sites:
            print("  [snd] no writes to the DMA buffer recorded yet")
            return
        print("\n=== sound DMA-buffer write sites (the mixer) ===")
        by_func = defaultdict(int)
        detail = defaultdict(list)
        for (cs, ip), n in self.sound_sites.most_common(40):
            off = cs * 16 + ip - self.image_base
            fn = find_function_start(img, off)
            by_func[fn] += n
            detail[fn].append((off, n))
        for fn, n in sorted(by_func.items(), key=lambda kv: -kv[1]):
            name = f"{fn:#07x}" if fn is not None else "  unknown"
            sites = ", ".join(f"{o:#07x}({c})" for o, c in detail[fn][:4])
            print(f"  {name:>10}  {n:>10} bytes  {sites}")

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
        table = list(NATIVE_TABLE)
        if self.native_sound:
            table += SOUND_NATIVES
        if self.native_mouse:
            table += MOUSE_NATIVES
        if self.native_keyboard:
            table += KEYBOARD_NATIVES
        if self.native_file:
            table += FILE_NATIVES
        if self.native_xms:
            table += XMS_NATIVES
        for off, name, fn, kind in table:
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
        if result is DECLINE:
            # Hand back to the original body: some cases (error paths that set
            # errno through the runtime's own helper) must not be faked.
            self.native_declined += 1
            return

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

    def cached_read(self, addr, n):
        """Read guest memory, caching it until the guest writes to that page.

        Now that every drawing routine is native, the graphics data those
        routines consume - sprite pixels, tile rows, row-pointer tables - is
        loaded from the egg once and thereafter only read. Fetching it through
        uc.mem_read on every call was the dominant remaining cost, and it was
        re-fetching bytes that had not changed.

        A page-granular write hook invalidates the cache, so this stays correct
        if the game ever does rewrite that data (loading a new level, say)
        rather than assuming it never will. Only source data is cached: DGROUP
        globals change constantly and are deliberately left alone, since
        caching them would invalidate every frame and add a write hook to the
        busiest page in the program.
        """
        key = (addr, n)
        hit = self._cache.get(key)
        if hit is not None:
            self.cache_hits += 1
            return hit
        self.cache_misses += 1
        data = bytes(self.uc.mem_read(addr, n))
        self._cache[key] = data
        for page in range(addr >> 12, (addr + n - 1 >> 12) + 1):
            self._cache_pages.setdefault(page, set()).add(key)
            if page not in self._cache_hooks:
                lo = page << 12
                self._cache_hooks[page] = self.uc.hook_add(
                    UC_HOOK_MEM_WRITE, self._invalidate, None, lo, lo + 0xFFF)
        return data

    def _invalidate(self, uc, access, address, size, value, user):
        keys = self._cache_pages.get(address >> 12)
        if keys:
            for k in keys:
                self._cache.pop(k, None)
            keys.clear()
            self.cache_drops += 1

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
    raw = m.cached_read(table, n * 4)
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
            blob = m.cached_read(rows[0] + start, (n - 1) * stride + span)
            return [blob[i * stride:i * stride + span] for i in range(n)]
    return [m.cached_read(r + start, span) for r in rows]


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
            bg = np.frombuffer(m.cached_read(bg_rows[by], mask_x + 1), dtype=np.uint8)
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
    data = m.cached_read(pixels, w * h + src + 1) if w and h else b""
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


def native_play_sample(m, args):
    """play_sample(desc_far, id, loop) at 0x151d2 -> pygame."""
    desc_off, desc_seg = m.arg16(args, 0), m.arg16(args, 1)
    sid, loop = m.arg16(args, 2), m.arg16(args, 3)
    return m.voices.play_sample(desc_off, desc_seg, sid, loop)


def native_stop_voice(m, args):
    """stop_voice(slot) at 0x15176."""
    m.voices.stop_voice(m.arg16(args, 0))
    return None


def native_stop_by_id(m, args):
    """stop_sound_by_id(id) at 0x15267."""
    m.voices.stop_by_id(m.arg16(args, 0))
    return None


def native_is_playing(m, args):
    """is_sound_playing(id) at 0x15298."""
    return m.voices.is_playing(m.arg16(args, 0))


def native_mix_voice(m, args):
    """Neutralise the per-voice mixer at 0x156cc.

    pygame is doing the playback now, so this must NOT accumulate into the mix
    buffer - otherwise the same sample also reaches the DSP path and you hear it
    twice, slightly out of step.
    """
    return None


DECLINE = object()      # a native returning this lets the original body run


def native_dos_read(m, args):
    """read(fd, buf_far, count) at 0x14a3 - the raw DOS read wrapper.

    The layers above this (fread buffering, text-mode CR stripping, Ctrl-Z EOF)
    keep working untouched; only the INT 21h AH=3Fh round-trip is replaced,
    served from the file image our DOS shim already holds.

    Declines rather than guesses when the handle is unknown or marked
    write-only: the original sets errno through its own helper, and faking that
    would be wrong in a way the game could act on.
    """
    fd = m.arg16(args, 0)
    buf_off, buf_seg, count = m.arg16(args, 1), m.arg16(args, 2), m.arg16(args, 3)
    flags = struct.unpack("<H", m.read(m.dgroup_base + 0x2F6E + fd * 2, 2))[0]
    h = m.handles.get(fd)
    if h is None or (flags & 2):
        return DECLINE
    chunk = bytes(h.data[h.pos:h.pos + count])
    if chunk:
        m.write(buf_seg * 16 + buf_off, chunk)
    m.file_reads += 1
    m.file_bytes += len(chunk)
    if m.trace_file:
        m.egg_access.append((h.path, h.pos, len(chunk)))
    h.pos += len(chunk)
    return len(chunk)


def native_dos_lseek(m, args):
    """lseek(fd, off_lo, off_hi, whence) at 0x12eb, returning DX:AX."""
    fd = m.arg16(args, 0)
    lo, hi, whence = m.arg16(args, 1), m.arg16(args, 2), m.arg16(args, 3) & 0xFF
    h = m.handles.get(fd)
    if h is None:
        return DECLINE
    # The original clears the EOF flag; omitting that would leave a stale EOF
    # and the next read would wrongly report end of file.
    a = m.dgroup_base + 0x2F6E + fd * 2
    f = struct.unpack("<H", m.read(a, 2))[0]
    m.write(a, struct.pack("<H", f & 0xFDFF))
    off = (hi << 16) | lo
    if off >= 1 << 31:
        off -= 1 << 32
    base = {0: 0, 1: h.pos, 2: len(h.data)}.get(whence, 0)
    h.pos = max(0, min(base + off, len(h.data)))
    m.file_seeks += 1
    return (h.pos & 0xFFFF, (h.pos >> 16) & 0xFFFF)


def native_kbhit(m, args):
    """Borland kbhit() at 0x029fc - the single choke point for key polling.

    The game reaches the keyboard only through DOS check-stdin (no INT 16h, no
    INT 09h hook), from many different call sites, so there is no wrapper worth
    replacing - but they all funnel through this one library routine, called
    ~308k times a session.

    Faithful to two details: the pushback buffer at [0x30c6] is consulted first
    and returns 1, and the DOS path sign-extends AL so "key available" is 0xffff
    rather than 1. The routine is frameless and takes no arguments.
    """
    if m.read(m.dgroup_base + 0x30C6, 1)[0] != 0:
        return 1
    # Must also report ready while the scancode half of an extended key is
    # still pending, or the game stops asking before collecting it.
    ready = bool(m.key_buf) or m.pending_scan is not None
    return 0xFFFF if ready else 0


def native_mouse_motion(m, args):
    """mouse_motion(int far *dx, int far *dy) at 0x0675b (INT 33h AX=0x0b).

    Reports whole mickeys and carries the remainder, exactly as the INT 33h
    handler did - the game integrates these to position its own cursor and never
    asks for an absolute position, so dropping fractions loses fine control.
    """
    dx_off, dx_seg = m.arg16(args, 0), m.arg16(args, 1)
    dy_off, dy_seg = m.arg16(args, 2), m.arg16(args, 3)
    idx, idy = int(m.mouse_rel[0]), int(m.mouse_rel[1])
    m.mouse_rel[0] -= idx
    m.mouse_rel[1] -= idy
    m.write(dx_seg * 16 + dx_off, struct.pack("<h", max(-32768, min(32767, idx))))
    m.write(dy_seg * 16 + dy_off, struct.pack("<h", max(-32768, min(32767, idy))))
    return None


def native_mouse_presses(m, args):
    """mouse_presses(button) at 0x0678e (INT 33h AX=5): count since last asked."""
    b = min(m.arg16(args, 0), 2)
    n = m.press_count[b]
    m.press_count[b] = 0
    return n


def native_mouse_releases(m, args):
    """mouse_releases(button) at 0x067ba (INT 33h AX=6)."""
    b = min(m.arg16(args, 0), 2)
    n = m.release_count[b]
    m.release_count[b] = 0
    return n


def native_sound_gather(m, args):
    """Native replacement for the sample gather at 0x157c1. Takes no arguments.

    Not a mixer despite appearances: for each of 256 output bytes it reads a
    16-bit offset from a table and copies that byte of the sample buffer, which
    is how pitch and looping are expressed. Volume and voice mixing happen
    elsewhere.

    The original is careful with segments and the native has to match:

        mov ss, ds          -> SS = DGROUP, so [bp] reads the offset table
        lds si, [0x2b42]    -> DS:SI = sample base; DS is NOT DGROUP after this
        les di, [0x3969]    -> read through the NEW DS, i.e. the sample segment
        mov al, [bx + si]   -> 16-bit offset arithmetic, wraps in the segment

    Note this is ~43 calls/second writing 11 KB/s, against 4.5 MB/s for
    graphics, so it is converted for consistency rather than for speed.
    """
    g = m.dgroup_base
    soff, sseg = struct.unpack("<HH", m.read(g + 0x2B42, 4))
    sbase = sseg * 16

    # The table encodes the current playback position, so it changes every call
    # and must not be cached.
    tbl = np.frombuffer(m.read(g + 0x3977, 512), dtype=np.uint16)

    # Destination pointer is read through the sample segment, as above.
    doff, dseg = struct.unpack("<HH", m.read(sbase + 0x3969, 4))
    dest = dseg * 16 + doff

    idx = (tbl.astype(np.uint32) + soff) & 0xFFFF
    lo, hi = int(idx.min()), int(idx.max())
    src = np.frombuffer(m.read(sbase + lo, hi - lo + 1), dtype=np.uint8)
    m.write(dest, src[idx - lo].tobytes())
    m.native_pixels += 0                 # not pixels; keep the counter honest
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
        src = np.frombuffer(m.cached_read(far(table + srcrow * 4) + plane, n * 4),
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
            bg = np.frombuffer(m.cached_read(bg_rows[by], mask_x + 1), dtype=np.uint8)
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
        data = m.cached_read(src, n * 4)[::4]
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
def native_xms_present(m, args):
    """XMS installation check: INT 2Fh AX=4300h, AL=80h if a driver is there.

    We are the driver, so the answer is a constant. Worth keeping rather than
    returning 0: the game disables sound entirely without XMS.
    """
    return 1


def native_xms_get_entry(m, args):
    """Fetch the driver entry point and cache it where the game expects it.

    INT 2Fh AX=4310h returns the far pointer in ES:BX; the original then stores
    it at DGROUP:0x2b46, and every subsequent XMS request is an lcall through
    that slot. Writing it directly is the whole function.
    """
    m.uc.mem_write(m.dgroup_base + 0x2B46,
                   struct.pack("<HH", 0, emulation.XMS_STUB_SEG))
    return None


NATIVE_TABLE = [
    (0x05D3A, "compose_layer", native_compose_layer, "far"),
    (0x063D6, "draw_sprite", native_draw_sprite, "far"),
    (0x05C09, "blit_rows", native_blit_rows, "far"),
    (0x05DC4, "compose_scroll", native_compose_scroll, "far"),
    (0x05AC2, "blit_rows_masked", native_blit_rows_masked, "far"),
    (0x05761, "plot_pixel", native_plot_pixel, "far"),
    (0x04D2A, "clear_vram", native_clear_vram, "far"),
    (0x157C1, "sound_gather", native_sound_gather, "far"),
]

# Enabled only with --native-sound. The whole family must go together: the game
# queries and stops sounds by id and reads an active-voice count, so pygame and
# the guest's voice table have to agree or sounds stop starting.
# Enabled with --native-file. The raw DOS read/lseek wrappers only; fread
# buffering, text-mode translation and open() are left to the original, which
# keeps their semantics without having to model Borland's FILE structure.
# Enabled with --native-xms, alongside servicing the driver entry as a code hook
# rather than an interrupt. These two are the only INT 2Fh sites in the binary:
# the detection pair the game calls once at startup before caching the driver
# entry at DGROUP:0x2b46, from where every later request is an lcall.
XMS_NATIVES = [
    (0x159AE, "xms_present", native_xms_present, "far"),
    (0x159C7, "xms_get_entry", native_xms_get_entry, "far"),
]

FILE_NATIVES = [
    (0x014A3, "dos_read", native_dos_read, "far"),
    (0x012EB, "dos_lseek", native_dos_lseek, "far"),
]

# Enabled with --native-keyboard. One entry covers all key polling: every call
# site reaches the keyboard through this library routine.
KEYBOARD_NATIVES = [
    (0x029FC, "kbhit", native_kbhit, "far"),
]

# Enabled with --native-mouse. These three are the game's entire mouse input:
# it never asks for an absolute position, so replacing them removes all INT 33h
# traffic (2.69M calls in one session) and the int86 shim behind it.
MOUSE_NATIVES = [
    (0x0675B, "mouse_motion", native_mouse_motion, "far"),
    (0x0678E, "mouse_presses", native_mouse_presses, "far"),
    (0x067BA, "mouse_releases", native_mouse_releases, "far"),
]

SOUND_NATIVES = [
    (0x151D2, "play_sample", native_play_sample, "far"),
    (0x15176, "stop_voice", native_stop_voice, "far"),
    (0x15267, "stop_sound_by_id", native_stop_by_id, "far"),
    (0x15298, "is_sound_playing", native_is_playing, "far"),
    (0x156CC, "mix_voice", native_mix_voice, "far"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="../Ducks.exe")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--chunk", type=int, default=400_000)
    ap.add_argument("--blaster", action="store_true")
    ap.add_argument("--profile", action="store_true",
                    help="report which routines do the drawing, then exit")
    ap.add_argument("--profile-sound", action="store_true",
                    help="profile writes to the sound DMA buffer from the start")
    ap.add_argument("--native-file", action="store_true",
                    help="serve the raw DOS read/lseek wrappers natively")
    ap.add_argument("--native-keyboard", action="store_true",
                    help="serve kbhit() natively, removing all key polling "
                         "through DOS")
    ap.add_argument("--native-mouse", action="store_true",
                    help="serve the game's mouse wrappers natively, removing "
                         "all INT 33h traffic")
    ap.add_argument("--trace-file", action="store_true",
                    help="record which functions do file I/O, and how much")
    ap.add_argument("--trace-keyboard", action="store_true",
                    help="record which game functions poll the keyboard")
    ap.add_argument("--trace-mouse", action="store_true",
                    help="record which game functions poll INT 33h")
    ap.add_argument("--sound-bank", action="store_true",
                    help="capture samples into an indexed bank as they load")
    ap.add_argument("--native-sound", action="store_true",
                    help="play voices through pygame instead of the emulated "
                         "Sound Blaster path")
    ap.add_argument("--observe-play", action="store_true",
                    help="decode the descriptor passed to play_sample")
    ap.add_argument("--watch-writers", default="",
                    help="DGROUP range lo,hi to count writers of, "
                         "e.g. 0x3c78,0x3cb0")
    ap.add_argument("--trace-calls", default="",
                    help="comma-separated image offsets to trace callers of, "
                         "e.g. 0x155be,0x157c1")
    ap.add_argument("--verify", action="store_true",
                    help="run each native alongside the code it replaces and "
                         "diff the result (slow; correctness check only)")
    ap.add_argument("--keep-diagnostics", action="store_true",
                    help="keep the inherited per-write instrumentation on")
    ap.add_argument("--sound-slices", type=int, default=32,
                    help="times per display update to service the sound card")
    ap.add_argument("--native-xms", action="store_true",
                    help="service XMS with no interrupts: driver entry as a code "
                         "hook, plus the two INT 2Fh detection sites")
    ap.add_argument("--run-seconds", type=float, default=0.0,
                    help="quit cleanly after N seconds, for measurement runs")
    ap.add_argument("--read-only", action="store_true",
                    help="keep saves in memory only, never write the game dir")
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
               native_sound=args.native_sound,
               native_mouse=args.native_mouse,
               native_keyboard=args.native_keyboard,
               native_file=args.native_file, native_xms=args.native_xms,
               persist=not args.read_only,
               max_insns=1 << 62)
    m.voices = NativeVoices(m, bank=m.bank) if args.native_sound else None
    if args.native_sound or args.sound_bank:
        m.capture_loader()
    if args.native_xms:
        m.install_native_xms()
    m.trace_mouse = args.trace_mouse
    m.trace_keyboard = args.trace_keyboard
    m.trace_file = args.trace_file
    # Kept in hand so the profile report can name functions at any moment.
    _d = open(args.unpacked, "rb").read()
    img = _d[struct.unpack_from("<13H", _d, 2)[3] * 16:]
    if args.profile_sound:
        m.profile_sound()
    if args.observe_play:
        m.observe_play_sample()
    if args.watch_writers:
        _lo, _hi = [int(x, 0) for x in args.watch_writers.split(",")]
        m.watch_writers(_lo, _hi)
    if args.trace_calls:
        m.add_call_tracers([int(x, 0) for x in args.trace_calls.split(",")
                            if x.strip()])
    print(f"=== native-I/O port: {len(m.natives)} routine(s) serviced "
          f"natively, everything else emulated ===")
    audio = None
    if args.blaster and not args.no_audio and not args.native_sound:
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
        if m.voices is not None:
            m.voices.reap()

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
                    m.sound_report(img)
                    m.xms_report(img)
                    m.call_report(img)
                    m.writer_report(img)
                    m.play_report()
                elif ev.key == pygame.K_F3:
                    m.profile_sound()
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

        if args.run_seconds and m._elapsed() >= args.run_seconds:
            print(f"  [stat] --run-seconds {args.run_seconds} reached, quitting")
            running = False
        if m._elapsed() >= next_status:
            next_status += args.status_every
            print(f"  [stat] t={m._elapsed():6.1f}s frames={frames} "
                  f"mode={m.mode:#04x} natives={dict(m.native_calls)}")

    m.flush_open_files()
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
    if m.sound_sites:
        m.sound_report(img)
    m.xms_report(img)
    m.call_report(img)
    m.writer_report(img)
    m.play_report()
    if m.voices is not None:
        import json
        print(f"  native voices   : {json.dumps(m.voices.summary())}")
    m.bank.report()
    m.mouse_report(img)
    m.kbd_report(img)
    m.int_report(img)
    m.file_report(img)
    if m.native_file:
        print(f"  native file I/O : {m.file_reads} reads ({m.file_bytes} bytes), "
              f"{m.file_seeks} seeks, {m.native_declined} declined")
    if m.file_ops:
        print(f"  file operations ({len(m.file_ops)}):")
        for op in m.file_ops[-40:]:
            print(f"    {op}")
    if m.files_persisted:
        print(f"  saved to disk   : {m.files_persisted}")
    elif m.overlay:
        print(f"  overlay files   : "
              f"{ {k: len(v) for k, v in m.overlay.items()} } (not persisted)")
    pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
