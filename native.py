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
import bisect
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
                 native_setup=False, skip_natives=(), persist=True, **kw):
        self.native_sound = native_sound
        self.native_mouse = native_mouse
        self.native_keyboard = native_keyboard
        self.native_file = native_file
        self.native_xms = native_xms
        self.native_setup = native_setup
        self.skip_natives = set(skip_natives)
        self.int_stubs = {}          # linear INT site -> interrupt number
        self.native_secs = defaultdict(float)   # routine -> seconds spent in it
        self.native_rows = Counter()            # routine -> inner iterations
        self.rows_done = 0                      # set by a handler, then banked
        self.native_fp = False       # set by install_native_fp()
        self.fp_sites = {}           # linear site -> interrupt it replaced
        self.fp_unknown = Counter()
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
        self.plot_pixel_warned = False
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
        self.traced_natives = set()
        self.call_tracers = defaultdict(Counter)
        self.call_args = defaultdict(Counter)
        self.writer_sites = Counter()
        self.writer_fields = Counter()
        self.play_calls = 0
        self.play_samples = Counter()
        self.mark_reads = self.mark_rb = 0
        self.mark_nt = 0.0
        self.verify = verify
        self.verify_only = set()
        self.verify_declined = 0
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

    # Borland's floating point, and how to undo it. The compiler emits every x87
    # instruction as FWAIT followed by an ESC opcode (0xD8-0xDF) plus its ModRM
    # and displacement. Linking against the emulator library overwrites just that
    # two-byte FWAIT+ESC pair with a two-byte INT, leaving the operand bytes
    # alone - which is why the interrupt number carries the opcode: INT 34h..3Bh
    # for D8..DF, and INT 3Dh for a lone FWAIT. Reversing it is a two-byte write.
    FP_ESC_LO, FP_ESC_HI = 0x34, 0x3B
    FP_WAIT = 0x3D

    def install_plane_loops(self):
        """Replace the guest's four-plane drawing loops with native ones.

        Hooked at the loop head rather than a function entry - the loop is inline
        inside a larger function, so the same technique as the interrupt stubs
        applies: do the work, then step IP to the loop exit.
        """
        for head, (exit_off, _) in PLANE_LOOPS.items():
            lin = self.image_base + head
            self.uc.hook_add(UC_HOOK_CODE, self._on_plane_loop, None, lin, lin)
            try:
                self.uc.ctl_remove_cache(lin, lin + 2)
            except Exception:
                pass
            print(f"  [loop] plane loop {head:#07x} native, exit {exit_off:#07x}")

    def _on_plane_loop(self, uc, address, size, user):
        head = address - self.image_base
        entry = PLANE_LOOPS.get(head)
        if entry is None:
            return
        exit_off, handler = entry
        label = f"plane_loop {head:#07x}"
        self.native_calls[label] += 1
        if self.verify and ("plane_loop" in self.verify_only
                            or not self.verify_only):
            return self._verify_plane_loop(uc, head, exit_off, handler)
        t0 = time.perf_counter()
        handler(self)
        dt = time.perf_counter() - t0
        self.native_secs[label] += dt
        # Into the total as well, or the report header excludes the loops - and
        # they are now where nearly all the drawing happens.
        self.native_time += dt
        # CS is unchanged, so the jump is just the distance between the two
        # offsets. The loop counter is left alone: it is a local that nothing
        # after the loop reads.
        self._set(UC_X86_REG_IP,
                  (self._reg(UC_X86_REG_IP) + (exit_off - head)) & 0xFFFF)

    def _verify_plane_loop(self, uc, head, exit_off, handler):
        """Run the native into a snapshot, let the guest's loop run, then diff.

        The function-level harness cannot be reused here: there is no return
        address to hook, so the comparison point is the loop's exit instruction.
        Plane-selection state is saved and restored too - unlike a blitter, this
        handler moves the sequencer mask as it goes.
        """
        before = [bytes(p) for p in self.planes]
        mask, active = self.map_mask, self.active_planes
        plane_var = self.read(self.dgroup_base + 0x177D, 1)
        try:
            handler(self)
        except Exception as e:
            print(f"  [verify] plane_loop {head:#07x} raised {e!r}")
            for i, p in enumerate(before):
                self.planes[i][:] = p
            return
        predicted = [bytes(p) for p in self.planes]
        for i, p in enumerate(before):
            self.planes[i][:] = p
        self.map_mask, self.active_planes = mask, active
        self.write(self.dgroup_base + 0x177D, plane_var)

        exit_lin = self.image_base + exit_off
        state = {"h": None}

        def on_exit(uc2, a2, s2, u2):
            if a2 != exit_lin:
                return
            uc2.hook_del(state["h"])
            self.verify_calls += 1
            bad = 0
            first = None
            for pi, (pa, pb) in enumerate(zip(predicted, self.planes)):
                for i, (a, b) in enumerate(zip(pa, pb)):
                    if a != b:
                        bad += 1
                        if first is None:
                            first = (pi, i, a, b)
            if bad:
                self.verify_bad += 1
                pi, i, a, b = first
                print(f"  [verify] plane_loop {head:#07x} MISMATCH {bad} "
                      f"bytes, first "
                      f"plane{pi} off {i:#07x} native={a:#04x} real={b:#04x}")
            else:
                print(f"  [verify] plane_loop {head:#07x}: match "
                      f"#{self.verify_calls}")

        # Counted here as well as in _verify_native: this is a comparison that
        # has been armed and not yet completed, which is exactly what the
        # coverage percentage in the exit report is measuring.
        self.verify_pending += 1
        state["h"] = uc.hook_add(UC_HOOK_CODE, on_exit, None, exit_lin, exit_lin)
        try:
            uc.ctl_remove_cache(exit_lin, exit_lin + 1)
        except Exception:
            pass

    def install_int_stubs(self):
        """Answer interrupts at the instruction itself, where no entry will do.

        These are inline in the C runtime, or buried deep inside a function,
        rather than behind a callable entry point,
        so they cannot be replaced at a function entry like every other native.
        They are answered at the instruction instead: service in Python, then step
        IP past the two bytes of the INT.

        Requires the unpacked image - run with --exe Ducks.unpacked.exe. Given
        the packed original, these addresses still hold compressed data when the
        hooks go in, because the machine starts on the DIET stub and the game only
        appears in memory part-way through the run; every site then fails
        verification and is skipped, which is why the count is worth reading.
        """
        ok, bad = 0, []
        for off, intno in STUBBED_INT_SITES:
            lin = self.image_base + off
            found = bytes(self.uc.mem_read(lin, 2))
            if found != bytes([0xCD, intno]):
                bad.append(f"{off:#07x} holds {found.hex()}, want cd{intno:02x}")
                continue
            self.int_stubs[lin] = intno
            self.uc.hook_add(UC_HOOK_CODE, self._on_int_stub, None, lin, lin)
            try:
                self.uc.ctl_remove_cache(lin, lin + 2)
            except Exception:
                pass
            ok += 1
        print(f"  [ints] {ok}/{len(STUBBED_INT_SITES)} interrupt site(s) "
              f"answered natively")
        if bad:
            print(f"  [ints] {len(bad)} skipped, the image is not in place yet "
                  f"- is this the packed Ducks.exe?")
            for b in bad[:3]:
                print(f"  [ints]   {b}")

    def _on_int_stub(self, uc, address, size, user):
        intno = self.int_stubs.get(address)
        if intno is None:
            return
        ah = (self._reg(UC_X86_REG_AX) >> 8) & 0xFF
        entry = INT_STUBS.get((intno, ah)) or INT_STUBS.get((intno, None))
        if entry is None:
            return                      # no stub for this function: let it trap
        name, fn = entry
        fn(self)
        self._set(UC_X86_REG_IP, (self._reg(UC_X86_REG_IP) + 2) & 0xFFFF)
        self.native_calls[name] += 1

    def install_native_fp(self):
        """Hand the game's floating point to the real FPU.

        Every FP operation currently traps into Borland's software emulator
        inside the binary - 24207 interrupts in a play session, each running a
        few hundred emulated instructions to do one multiply. Unicorn implements
        x87 in real mode, so the instructions the compiler originally wrote can
        simply be put back and executed.

        Sites patch themselves the first time they execute rather than in a
        static sweep: a two-byte scan for CD 34..CD 3B across 114 KB of a 16-bit
        image cannot tell code from data, and a false positive would corrupt
        whatever it hit. An interrupt that has just fired, by contrast, is proof
        that those bytes are a real instruction.

        Because patching happens before the emulator's handler ever runs, no
        operation executes against its in-memory FP stack, so the two
        representations never coexist.
        """
        # A real FPU powers up with control word 0x037f. Unicorn's starts at
        # zero, which selects single precision and unmasks every exception, so
        # run one FINIT before the game touches floating point. There is no
        # control-word register exposed to write it directly.
        scratch = 0x800
        saved = bytes(self.uc.mem_read(scratch, 2))
        cs, ip = self._reg(UC_X86_REG_CS), self._reg(UC_X86_REG_IP)
        self.uc.reg_write(UC_X86_REG_CS, 0)
        self.uc.mem_write(scratch, bytes([0xDB, 0xE3]))          # FINIT
        self.uc.emu_start(scratch, scratch + 2, count=1)
        self.uc.mem_write(scratch, saved)
        self.uc.reg_write(UC_X86_REG_CS, cs)
        self.uc.reg_write(UC_X86_REG_IP, ip)
        try:
            self.uc.ctl_remove_cache(scratch, scratch + 2)
        except Exception:
            pass
        self.native_fp = True
        print("  [fp] x87 enabled; FP sites will patch themselves on first use")

    def _patch_fp_site(self, uc, intno):
        """Rewrite one emulator interrupt back into the x87 instruction it was."""
        ip = self._reg(UC_X86_REG_IP)
        site = self._reg(UC_X86_REG_CS) * 16 + ((ip - 2) & 0xFFFF)
        if self.FP_ESC_LO <= intno <= self.FP_ESC_HI:
            repl = bytes([0x9B, 0xD8 + intno - self.FP_ESC_LO])
        elif intno == self.FP_WAIT:
            repl = bytes([0x9B, 0x90])
        else:
            # INT 3Ch (segment-override prefix plus ESC) and 3Eh, whose encodings
            # we have never seen execute and so cannot reverse with confidence.
            # Servicing one through the emulator would be worse than useless: its
            # FP stack lives in memory while every patched site uses the real
            # one, so from here the two silently disagree.
            if not self.fp_unknown[intno]:
                print(f"  [fp] INT {intno:02x}h at {site:#07x} is not a plain "
                      f"ESC opcode. Handing it to the emulator MIXES two "
                      f"floating-point states - results after this are suspect.")
            self.fp_unknown[intno] += 1
            return False
        uc.mem_write(site, repl)
        try:
            uc.ctl_remove_cache(site, site + 16)
        except Exception:
            pass
        self._set(UC_X86_REG_IP, (ip - 2) & 0xFFFF)   # rewind onto the new bytes
        self.fp_sites[site] = intno
        uc.emu_stop()          # the outer chunk loop resumes at the rewound IP
        return True

    def native_time_report(self):
        """Rank the natives by wall time, with what each call actually did.

        Calls per second cannot explain a stutter on its own: a routine called
        rarely but looping over 200 rows costs more than one called constantly
        over a handful of pixels. Rows per call separates those two.
        """
        if not self.native_secs:
            return
        el = max(1e-6, self._elapsed())
        print(f"\n=== time in natives, ranked ({self.native_time:.2f}s of "
              f"{el:.1f}s elapsed) ===")
        for name, secs in sorted(self.native_secs.items(), key=lambda kv: -kv[1]):
            n = self.native_calls.get(name, 0)
            if not n:
                continue
            rows = self.native_rows.get(name, 0)
            print(f"  {name:<18} {secs:7.2f}s {100 * secs / el:5.1f}% of run "
                  f"{n:>7} calls {1e6 * secs / n:8.1f} us/call"
                  + (f" {rows / n:6.1f} rows/call" if rows else ""))

    def fp_report(self, img):
        if not self.fp_sites and not self.fp_unknown:
            return
        print(f"\n=== floating point: {len(self.fp_sites)} sites now run on the "
              f"real FPU ===")
        by_fn = Counter()
        for site, intno in self.fp_sites.items():
            off = site - self.image_base
            f = find_function_start(img, off)
            by_fn[f if f is not None else off] += 1
        for f, n in by_fn.most_common(12):
            print(f"  {f:#07x}  {n} instruction(s)")
        if self.fp_unknown:
            print(f"  UNHANDLED, FP state mixed: {dict(self.fp_unknown)}")

    def _on_intr(self, uc, intno, user):
        if self.native_fp and 0x34 <= intno <= 0x3E:
            if self._patch_fp_site(uc, intno):
                return
        return self._record_intr(uc, intno, user)

    def _record_intr(self, uc, intno, user):
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
            for n, site in sorted(sites, key=lambda s: -s[0]):
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
            if off in self.natives:
                # Serviced natively: record from inside the native dispatcher,
                # before it returns to the caller.
                self.traced_natives.add(off)
                print(f"  [call] tracing calls to {off:#07x} (a native)")
                continue
            lin = self.image_base + off
            self.uc.hook_add(UC_HOOK_CODE, self._on_traced_call,
                             None, lin, lin)
            print(f"  [call] tracing calls to {off:#07x}")

    def _record_caller(self, off, ss, sp, ret_size):
        """Attribute a call to whoever made it, given an intact stack frame.

        Split out because tracing a routine that is ALSO a native cannot use a
        second code hook at the same address: the native hook is registered first,
        and by the time a later hook runs it has already returned to the caller by
        advancing SP, so the read lands past the return address and yields
        nonsense - negative offsets pointing into the interrupt vector table.
        """
        try:
            ip, cs = struct.unpack("<HH", self.uc.mem_read(ss * 16 + sp, 4))
            caller = cs * 16 + ip - self.image_base
        except Exception:
            caller = None
        self.call_tracers[off][caller] += 1
        try:
            args = struct.unpack("<4H",
                                 self.uc.mem_read(ss * 16 + sp + ret_size, 8))
            self.call_args[(off, caller)][args] += 1
        except Exception:
            pass

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
        if self.native_setup:
            table += SETUP_NATIVES
        for off, name, fn, kind in table:
            if name in self.skip_natives:
                continue
            self.natives[off] = (name, fn, kind)

    def _on_native(self, uc, address, size, user):
        off = address - self.image_base
        entry = self.natives.get(off)
        if entry is None:
            return
        name, handler, kind = entry
        self.native_calls[name] += 1

        # --verify-only names a subset to check even though it is in the skip
        # list, so a routine can be verified right after it is rewritten without
        # turning verification back on for everything.
        wanted = (name in self.verify_only if self.verify_only
                  else name not in VERIFY_SKIP)
        if self.verify and wanted:
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
        if off in self.traced_natives:
            self._record_caller(off, ss, sp, ret_size)

        t0 = time.perf_counter()
        result = handler(self, args_at)
        dt = time.perf_counter() - t0
        self.native_time += dt
        # Per-routine, not just the total: the total only says natives cost
        # something, which was never the question. Both perf_counter calls
        # already happen, so this is a dict add.
        self.native_secs[name] += dt
        self.native_rows[name] += self.rows_done
        self.rows_done = 0
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
            outcome = handler(self, args_at)
        except Exception as e:
            print(f"  [verify] {name}: native raised {e!r}")
            for i, p in enumerate(before):
                self.planes[i][:] = p
            return
        if outcome is DECLINE:
            # A declining native is not predicting anything, so there is nothing
            # to compare: it is handing the call back for the original to do.
            # Counting that as a mismatch made verification useless for exactly
            # the natives that need it most - the ones that only handle the cases
            # they have been proven correct on.
            self.verify_declined += 1
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


def compose_rows(m, fg_data, bg_rows, by, idx, mask_x):
    """Composite every row at once: the foreground wins unless it is zero.

    Done a row at a time this cost 7.4us for 80 pixels - six numpy operations on
    an 80-element array, where the per-call overhead dwarfs the arithmetic. As one
    2D operation the same work is a handful of calls for the whole region,
    measured at 12x on the compositing alone.

    `idx` is the background column for each output column: 1D when every row uses
    the same displacement, 2D when the background warp gives each row its own.
    """
    nrows = len(fg_data)
    fg = np.frombuffer(b"".join(fg_data), dtype=np.uint8).reshape(nrows, -1)[:, ::4]
    ncols = fg.shape[1]
    # Only the distinct background rows are read: the wrap mask means the same
    # few recur down the region, and each is a cached guest read.
    uniq, inv = np.unique(np.asarray(by, dtype=np.int32), return_inverse=True)
    bg = np.stack([np.frombuffer(m.cached_read(bg_rows[b], mask_x + 1),
                                 dtype=np.uint8) for b in uniq])
    rows = bg[inv]
    if idx.ndim == 1:
        sel = rows[:, idx[:ncols]]
    else:
        sel = np.take_along_axis(rows, idx[:, :ncols], axis=1)
    return np.where(fg == 0, sel, fg)


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


# Big enough for the largest function in this image: the in-game frame runs
# 0x0d7ee-0x0e8ac, 4287 bytes. The old window was 0x600, so addresses deep inside
# it resolved to nothing at all.
FUNCTION_SCAN_LIMIT = 0x2000

_fn_entries = {}
_fn_start_cache = {}
_fp_norm_cache = {}
_fn_map_cache = {}
_cs16 = None


def _disasm16():
    """A 16-bit capstone, or None if it is not installed.

    Imported lazily: this is only wanted to confirm a prologue for the exit
    reports, so the game should not pay for the import in order to run.
    """
    global _cs16
    if _cs16 is None:
        try:
            import capstone
            _cs16 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
            _cs16.detail = False
        except Exception:
            _cs16 = False
    return _cs16 or None


def _fp_normalised(img):
    """A copy of the image with Borland's INT-encoded x87 turned back into x87.

    INT 34h..3Bh stand in for FWAIT + ESC D8..DF, and INT 3Dh for a lone FWAIT;
    both are two bytes, as is the substitution, so nothing shifts. Built once per
    image and used only for sweeping - it is never executed.

    Safe because those vectors are Borland's floating point and nothing else in
    this binary: the interrupt inventory found no other use of 34h-3Dh.
    """
    key = len(img)
    norm = _fp_norm_cache.get(key)
    if norm is None:
        buf = bytearray(img)
        for i in range(len(buf) - 1):
            if buf[i] != 0xCD:
                continue
            n = buf[i + 1]
            if 0x34 <= n <= 0x3B:
                buf[i], buf[i + 1] = 0x9B, 0xD8 + n - 0x34
            elif n == 0x3D:
                buf[i], buf[i + 1] = 0x9B, 0x90      # FWAIT, then a NOP
        norm = bytes(buf)
        _fp_norm_cache[key] = norm
    return norm


def _entry_table(img):
    """Every `push bp; mov bp,sp` in the image, sorted, with a set for lookups.

    Built once - 423 of them here - so resolving an address is a bisect plus a
    confirmation or two. Confirming every byte match inside a window instead meant
    thousands of forward sweeps for any address sitting in data.
    """
    key = len(img)
    table = _fn_entries.get(key)
    if table is None:
        offs = [o for o in range(len(img) - 2)
                if img[o] == 0x55 and img[o + 1] == 0x8B and img[o + 2] == 0xEC]
        table = (offs, set(offs))
        _fn_entries[key] = table
    return table


def _sweep_end(md, norm, entries, start, limit):
    """Where the function at `start` ends, by sweeping to its terminating return.

    Borland emits one epilogue per function and packs functions back to back, so
    the end is the first return whose next byte begins another prologue match.
    None means no such return inside `limit` - the sweep desynced, or this is not
    a function at all.
    """
    for i in md.disasm(norm[start:start + limit], start):
        if i.mnemonic in ("ret", "retf") and (i.address + i.size) in entries:
            return i.address + i.size
    return None


def _function_map(img, limit=None):
    """Disjoint (start, end) spans for every real function, built once.

    Walking forward is what makes them disjoint, and disjoint is what makes them
    trustworthy: a prologue byte match that falls inside a function already
    measured is not an entry, whatever its bytes say. 28 of the 423 matches here
    are exactly that.

    Returns (starts, spans) for bisecting, or None when capstone is absent and
    nothing can be swept.
    """
    limit = FUNCTION_SCAN_LIMIT if limit is None else limit
    key = (len(img), limit)
    cached = _fn_map_cache.get(key)
    if cached is None:
        md = _disasm16()
        if md is None:
            return None
        offs, entries = _entry_table(img)
        norm = _fp_normalised(img)
        spans, i = [], 0
        while i < len(offs):
            start = offs[i]
            end = _sweep_end(md, norm, entries, start, limit)
            i += 1
            if end is None:
                continue
            spans.append((start, end))
            while i < len(offs) and offs[i] < end:
                i += 1        # inside this function, so not an entry
        cached = ([s for s, _ in spans], spans)
        _fn_map_cache[key] = cached
    return cached


def _enclosing(img, off, limit):
    """The (start, end) span containing `off`, or None."""
    themap = _function_map(img, limit)
    if themap is None:
        return None
    starts, spans = themap
    k = bisect.bisect_right(starts, off) - 1
    if k < 0:
        return None
    start, end = spans[k]
    return (start, end) if start <= off < end else None


def _find_function_start_bytes(img, off, limit):
    """The old backwards byte scan, for when capstone is not installed.

    Kept because it is better than nothing, and honest about being worse: it
    returns the nearest matching byte triple, which is only usually the entry.
    """
    for back in range(0, limit):
        i = off - back
        if i < 2:
            break
        if img[i] == 0x55 and img[i + 1] == 0x8B and img[i + 2] == 0xEC:
            return i
        if img[i] in (0xCB, 0xC3) and img[i + 1] == 0x55:
            return i + 1
    return None


def find_function_start(img, off, limit=FUNCTION_SCAN_LIMIT):
    """The Borland prologue beginning the function that contains `off`, or None.

    Answered from the function map, so an address is only reported as its own entry
    when it really starts a function - not merely because those three bytes read as
    a prologue. None means the address is in no function: padding, a jump table, or
    data.
    """
    key = (len(img), off, limit)
    if key in _fn_start_cache:
        return _fn_start_cache[key]
    span = _enclosing(img, off, limit)
    if span is None and _function_map(img, limit) is None:
        found = _find_function_start_bytes(img, off, limit)
    else:
        found = span[0] if span else None
    _fn_start_cache[key] = found
    return found


def function_extent(img, off, limit=FUNCTION_SCAN_LIMIT):
    """(start, end) of the function containing `off`; end is exclusive.

    Both ends come from the same forward walk, so extents cannot overlap and their
    sum cannot exceed the code they are measured from - which is how the old
    version's mistake surfaced, in coverage.py.

    Cross-checked against an independent rule, "the next prologue that resolves to
    itself"; test_fn_start.py keeps the two agreeing.

    (None, None) when `off` is in no function.
    """
    span = _enclosing(img, off, limit)
    return span if span else (None, None)

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
    idx = (np.arange(x0, width, 4, dtype=np.int32) & mask_x)

    if not fg_data or span == 0:
        return None
    by = (np.arange(height, dtype=np.int32) + y0) & mask_y
    out = compose_rows(m, fg_data, bg_rows, by, idx, mask_x).tobytes()
    for p in m.active_planes:
        m.planes[p][plane_off:plane_off + len(out)] = out
    m.native_pixels += len(out)
    m.rows_done = height
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
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    _i = u16(far(args + 0x06 - 6))
    return blit_sprite(m,
                       index=_i,
                       x=struct.unpack("<h", m.read(args + 0x0A - 6, 2))[0],
                       y=struct.unpack("<i", m.read(args + 0x0C - 6, 4))[0],
                       table=far(args + 0x10 - 6),
                       clip=far(args + 0x14 - 6),
                       colour=m.read(args + 0x18 - 6, 1)[0])


def blit_sprite(m, index, x, y, table, clip, colour):
    """Blit one sprite, given already-resolved arguments.

    Split out of the native above so the entity loop can draw without paying a
    second native dispatch per sprite - which is the whole point of replacing the
    loop rather than the blitter.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    desc = u16(table + 4) * 16 + u16(table + 2) + index * 14

    w, h = u16(desc + 0), u16(desc + 2)
    ox, oy = s16(desc + 4), s16(desc + 6)
    pixels = far(desc + 0x0A)

    # While a rate bucket is open, record what is being drawn. Knowing the
    # count is not enough to explain a redraw storm; the sprite identity and
    # size say whether it is one big image, a row of letters, or a whole scene.
    if m.active_label is not None:
        m.sprite_census[m.active_label][(index, w, h)] += 1

    x = x - ox
    y = y + s16(clip + 0) - oy

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

    # Only columns where x & 3 == plane are written, so the row is stepped by 4
    # from the first such column instead of testing every pixel.
    first = x + ((plane - x) & 3)
    ncols = max(0, (x_end - first + 3) // 4)
    nrows = y_end - y
    if ncols == 0 or nrows <= 0:
        return None

    # Source rows are a constant stride apart and the selected pixels are every
    # fourth byte, so the whole sprite is one strided 2D view - no copy, no loop.
    rowstride = (x_end - x) + row_extra
    lo = src + (first - x)
    need = (nrows - 1) * rowstride + (ncols - 1) * 4 + 1
    if lo < 0 or rowstride <= 0 or lo + need > len(buf):
        return None

    sel = np.lib.stride_tricks.as_strided(buf[lo:], shape=(nrows, ncols),
                                          strides=(rowstride, 4))
    nz = sel != 0
    vals = (sel.astype(np.uint16) + colour).astype(np.uint8)

    # first & 3 equals plane & 3, so (first + 4 * col) >> 2 is exactly
    # (first >> 2) + col: the destination is a plain strided block, one byte per
    # selected pixel, and needs no scatter indices at all.
    dst = plane_off + base + y * stride + (first >> 2)
    if dst < 0:
        return None
    for pl in (m.planes[p] for p in m.active_planes):
        fit = min(nrows, (len(pl) - dst - ncols) // stride + 1)
        if fit <= 0:
            continue
        view = np.frombuffer(pl, dtype=np.uint8)
        out = np.lib.stride_tricks.as_strided(view[dst:], shape=(fit, ncols),
                                              strides=(stride, 1))
        np.copyto(out, vals[:fit], where=nz[:fit])
    drawn = int(nz.sum())
    m.rows_done = nrows
    m.native_pixels += drawn
    return None


def native_draw_number(m, args):
    """Native replacement for the fixed-width number drawer at 0x0bb3b.

        [+0x06] word    : value
        [+0x08] word    : x of the leftmost digit cell
        [+0x0a] word    : y, sign-extended to a long by the original
        [+0x0c] far ptr : viewport / clip rectangle
        [+0x10] byte    : colour offset, as draw_sprite takes it
        [+0x12] word    : how many digits to draw

    The score and the level counter, drawn eight times a frame - not hot. It is
    here because it was the last thing inside the scroll caller's four-plane loop
    that was not native, and that loop is the point.
    """
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return draw_number(m, value=s16(args + 0x00), x=s16(args + 0x02),
                       y=s16(args + 0x04), clip=far(args + 0x06),
                       colour=m.read(args + 0x0A, 1)[0],
                       digits=s16(args + 0x0C))


def draw_number(m, value, x, y, clip, colour, digits):
    """Draw `digits` decimal digits of `value`, right-aligned, 12 pixels apart.

    Every digit is a sprite: glyph 0x71 plus the digit, out of the same table
    header at DGROUP:0x18e9 the entity loop draws from. The original walks from
    the least significant digit upwards, drawing at x + i * 12 with i counting
    down from digits - 1, so the field is fixed-width and right-aligned with no
    leading-zero suppression: a score of nothing draws six noughts.

    Goes through blit_sprite rather than the draw_sprite native, so a six-digit
    score costs one native dispatch instead of seven.
    """
    g = m.dgroup_base
    for i in range(digits - 1, -1, -1):
        # idiv truncates toward zero; Python's // floors. No caller passes a
        # negative today, but the two disagree silently on both the digit and
        # the quotient, so the original's arithmetic is what gets reproduced.
        q = -(-value // 10) if value < 0 else value // 10
        blit_sprite(m, index=(0x71 + value - q * 10) & 0xFFFF,
                    x=x + i * 12, y=y, table=g + 0x18E9, clip=clip,
                    colour=colour)
        value = q



def native_draw_number2(m, args):
    """Native replacement for the HUD's number drawer at 0x0d757.

        [+0x06] word : value      [+0x08] word : digits
        [+0x0a] word : x          [+0x0c] word : y, sign-extended by the original

    The same digit layout as draw_number at 0x0bb3b - glyph 0x71 plus the digit,
    12 pixels apart, least significant first, no leading-zero suppression - but
    with the clip, sprite table and colour fixed, and glyph 0x70 drawn behind each
    digit first. That backdrop is the visible difference between the HUD's numbers
    and the in-game frame's.
    """
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]
    return draw_number2(m, value=s16(args + 0x00), digits=s16(args + 0x02),
                        x=s16(args + 0x04), y=s16(args + 0x06))


def draw_number2(m, value, digits, x, y):
    """`digits` digits of `value`, each on a glyph-0x70 tile, 12 pixels apart."""
    g = m.dgroup_base
    for i in range(digits - 1, -1, -1):
        q = -(-value // 10) if value < 0 else value // 10
        # The tile goes down first and the digit over it, which is the order the
        # original draws them in and the only order that leaves the digit visible.
        for index in (0x70, (0x71 + value - q * 10) & 0xFFFF):
            blit_sprite(m, index=index, x=x + i * 12, y=y, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
        value = q


# All entity types are handled and verified. Kept as a knob because restricting
# it is how a suspect type gets isolated: set it to a frozenset and every call
# containing anything else declines to the emulated body.
ENTITY_TYPES_VERIFIED = None


def native_outline_sprite(m, args):
    """The outline drawer at 0x065f1, reading its arguments off the stack."""
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return outline_sprite(m, index=u16(far(args + 0)),
                          x=struct.unpack("<h", m.read(args + 4, 2))[0],
                          y=struct.unpack("<h", m.read(args + 6, 2))[0],
                          table=far(args + 8), clip=far(args + 0x0C))


def outline_sprite(m, index, x, y, table, clip):
    """Halo a sprite: a colour-0 pixel above, below, left and right of each of
    its non-zero pixels. 0x065f1.

    Same descriptors and the same clipping shape as draw_sprite, but it plots
    through the [0x53e] callback instead of writing spans - so, like the particle
    loop, only pixels whose x & 3 matches the selected plane land, and nothing is
    clipped beyond what the loop bounds impose.

    Two quirks are faithful rather than tidy: vertical clipping insets by a row
    at each end (y becomes top + 1, bottom becomes limit - 1), and clip[+4] is
    added to x after the source offsets are worked out, which shifts the sprite
    horizontally and therefore also changes which plane each pixel belongs to.
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    g = m.dgroup_base
    desc = u16(table + 4) * 16 + u16(table + 2) + index * 14
    w, h = u16(desc + 0), u16(desc + 2)
    if not (w and h) or not m.active_planes:
        return None
    pixels = u16(desc + 0x0A) + u16(desc + 0x0C) * 16
    xbase = m.read(clip + 4, 1)[0]

    src, row_extra = 0, 0
    x -= s16(desc + 4)
    top = s16(clip + 0)
    y += top - s16(desc + 6)
    right, bottom = x + w, y + h

    if x < 1:
        row_extra -= x - 1
        src -= x - 1
        x = 1
    elif right > 0x13F:
        row_extra += right - 0x13F
        right = 0x13F
    if top >= y:
        src -= (y - top - 1) * w
        y = top + 1
    elif s16(clip + 2) <= bottom:
        bottom = s16(clip + 2) - 1
    x += xbase
    right += xbase

    ncols, nrows = right - x, bottom - y
    stride_src = ncols + row_extra
    if ncols <= 0 or nrows <= 0 or stride_src <= 0 or src < 0:
        return None
    need = (nrows - 1) * stride_src + ncols
    data = m.cached_read(pixels + src, need)
    if len(data) < need:
        return None
    sel = np.lib.stride_tricks.as_strided(
        np.frombuffer(data, dtype=np.uint8), shape=(nrows, ncols),
        strides=(stride_src, 1))
    nz = sel != 0
    if not nz.any():
        return None
    m.native_calls["outline(inline)"] += 1
    if m.native_calls["outline(inline)"] == 1:
        # Written from the disassembly and never once executed, so it has never
        # been compared against the original. Say so the moment it runs.
        print("  [outline] the sprite outline is running for the first time. "
              "This path is UNVERIFIED - no session has contained an entity of "
              "type 0x0f or 0x10.\n"
              "  [outline] check it with --verify-only draw_entities,"
              "outline_sprite before trusting the screen. If it mismatches, "
              "doubt the vertical clip first: it insets by a row at each end "
              "(y = top + 1, bottom = limit - 1), and clip[+4] shifts x after "
              "the source offsets are computed, which also moves pixels between "
              "planes.")

    plane = m.read(g + 0x177D, 1)[0] & 3
    dst_off, dst_seg = struct.unpack("<HH", m.read(g + 0x16F1, 4))
    plane_off = dst_seg * 16 + dst_off - 0xA0000
    if plane_off < 0:
        return None
    stride = 90 if u16(g + 0x4FE) else 80
    base = plane_off + u16(g + 0x1727)
    sx = x + np.arange(ncols, dtype=np.int32)
    sy = y + np.arange(nrows, dtype=np.int32)
    planes = [m.planes[p] for p in m.active_planes]
    for dx, dy in ((0, -1), (0, 1), (1, 0), (-1, 0)):
        px = (sx + dx)[None, :]
        py = (sy + dy)[:, None]
        keep = nz & ((px & 3) == plane)
        if not keep.any():
            continue
        offs = np.broadcast_to(base + py * stride + (px >> 2),
                               (nrows, ncols))[keep]
        for pl in planes:
            inb = (offs >= 0) & (offs < len(pl))
            np.frombuffer(pl, dtype=np.uint8)[offs[inb]] = 0
        m.native_pixels += int(keep.sum())
    return None


def native_draw_entities(m, args):
    """Stack-reading shim for 0x0aba5."""
    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return draw_entities(m, scene=far(args + 0), view=args + 4,
                         colour=m.read(args + 0x18, 1)[0])


def draw_entities(m, scene, view, colour):
    """The sprite entity loop at 0x0aba5, one level above draw_sprite.

    Walks the scene's entity array, works out which sprite each one shows, and
    blits it. Called once per Mode X plane, like everything else in that loop.

        args+0   far ptr -> scene: +2 entity count, +8 far ptr -> entity array
        args+4   20-byte viewport, by value: +0 top, +2 bottom, +8 right,
                 +0xc scroll x, +0x10 scroll y (dword). Its address is what
                 draw_sprite receives as its clip rectangle.
        args+0x18 byte : colour offset

        entity record, 0x29 bytes:
          +0x00 word   x                  +0x14 byte   animation frame
          +0x04 dword  y, fixed point     +0x17 word   flag (0x36 y-nudge)
          +0x1f word   sprite sub-index   +0x23 word   y offset (type 0x26)
          +0x25 word   type

    Sprite selection is one mechanism with per-type exceptions. DGROUP 0x9a holds
    a far pointer per type, each addressing an array of sprite indices selected
    by (ent[+0x1f] & 0xfffe); [0x3a7] holds a flags byte per type whose bit 2
    means "a mirrored variant lives in the next slot", chosen by the global facing
    direction in [0x511]. Types 1, 2 and 4 compute an index arithmetically
    instead, and 0x26 and 0x36 adjust y first.

    Two paths are deliberately NOT reproduced, and the whole call declines if any
    entity needs one:

      * type 5 with y <= 0 calls 0x78d4, which mutates game state. Faking a
        drawing routine is safe; faking a state change is not.
      * an entity following one of type 0x0f or 0x10 is also drawn through
        0x65f1, which is not a blitter but an outline: for every non-zero pixel
        of the sprite it plots four colour-0 pixels around it, through the same
        [0x53e] callback the particles use. Not reimplemented yet, and it is what
        most declines are - a scene usually contains one highlighted entity, so
        the entity after it takes this path.

    Declining the entire call rather than the entity keeps this exactly as correct
    as the original: the emulated body runs and draws everything.
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    g = m.dgroup_base
    count = s16(scene + 2)
    if count <= 0:
        return None
    ents = far(scene + 8)
    scroll_x = s16(view + 0x0C)
    scroll_y = struct.unpack("<i", m.read(view + 0x10, 4))[0]

    recs = m.read(ents, count * 0x29)
    types = [struct.unpack_from("<H", recs, i * 0x29 + 0x25)[0]
             for i in range(count)]
    if ENTITY_TYPES_VERIFIED is not None and \
            any(t not in ENTITY_TYPES_VERIFIED for t in types):
        # Verified types only. The default branch's mirrored variants were
        # producing the right sprite indices at the right positions, yet the
        # original went on to emit further blits with a different clip rectangle
        # and colour that this loop does not - so something in that path draws
        # more than one sprite per entity, and it is not reproduced yet.
        return DECLINE

    # Bail out before drawing anything if any entity needs a path we do not have.
    del types  # rebuilt below; retiring an entity changes its type

    facing = u16(g + 0x511)
    frame_dir = -1 if facing else 1
    anim_base = m.read(g + 0x179E, 1)[0]

    def table_index(t, sub):
        ptr = far(g + 0x9A + (t & 0xFFFF) * 4)
        return u16(ptr + (sub & 0xFFFE))

    shadow = False
    for i in range(count):
        r = i * 0x29
        t = struct.unpack_from("<H", recs, r + 0x25)[0]
        sub = struct.unpack_from("<H", recs, r + 0x1F)[0]
        # Signed: the original sign-extends this byte before using it, so a
        # negative frame both shifts differently and is what the mirrored-variant
        # test compares against.
        frame = struct.unpack_from("<b", recs, r + 0x14)[0]
        y = struct.unpack_from("<i", recs, r + 4)[0]

        if t == 5 and y < 0:
            # 0x78d4: retire the entity in place - set its type and clear the
            # sub-index - then draw it as the type it has just become. A drawing
            # pass that mutates game state, which is why it was declined until
            # its 35 bytes were read: an entity that has floated off the top of
            # the screen is turned into an inactive type 0 rather than removed
            # from the array. Guarded on y < 0, not <= 0: the original only takes
            # this path when the high word of y is negative.
            if t != 0:
                m.write(ents + r + 0x25, b"\x00\x00")
                m.write(ents + r + 0x1F, b"\x00\x00")
            t, sub = 0, 0

        if t in (1, 2):
            # Arithmetic rather than a table, and mirrored by the facing flag.
            idx = ((frame << 2) + (2 - t) * 12 + anim_base + 6) & 0xFFFF
            if not facing:
                idx = (((2 - t) * 12 + 6) - (frame << 2) + anim_base) & 0xFFFF
        elif t == 4:
            if frame:
                idx = 0x7B + (1 if frame < 0 else 0)
            else:
                idx = table_index(4, sub)
        elif t == 0x26:
            y -= struct.unpack_from("<H", recs, r + 0x23)[0]
            idx = table_index(0x26, sub)
        elif t == 0x36:
            idx = table_index(0x36, sub)
            if struct.unpack_from("<H", recs, r + 0x17)[0] != 1:
                y += 8
        else:
            slot = t
            if m.read(g + 0x3A7 + (t & 0xFFFF), 1)[0] & 4:
                slot = t + (1 if frame == frame_dir else 0)
            idx = table_index(slot, sub)

        ex = struct.unpack_from("<h", recs, r)[0] - scroll_x
        if shadow:
            # The entity after one of type 0x0f/0x10 is outlined first, and gets
            # only the low word of y - unlike the blit below, which gets all 32
            # bits of it.
            y16 = ((y & 0xFFFF) - (scroll_y & 0xFFFF)) & 0xFFFF
            outline_sprite(m, index=idx, x=ex,
                           y=y16 - 0x10000 if y16 >= 0x8000 else y16,
                           table=g + 0x18E9, clip=view)
        shadow = t in (0x0F, 0x10)
        blit_sprite(m, index=idx, x=ex,
                    # ds:0x18e9 is pushed as the pointer VALUE, so the table
                    # header IS at 0x18e9 - not a pointer stored there.
                    y=y - scroll_y, table=g + 0x18E9, clip=view,
                    colour=colour)
    m.rows_done = count
    return None


def set_plane(m, n):
    """0x057ee: select a Mode X plane - the DGROUP copy and the sequencer mask.

    Goes through the same sequencer handler the OUT instructions reach, rather
    than assigning active_planes directly, so there is one definition of what a
    map-mask write means.
    """
    m.write(m.dgroup_base + 0x177D, bytes([n & 0xFF]))
    m._seq_write(0x02, 1 << (n & 3))


def compose_layer_shared(m):
    """Everything compose_layer computes that does not depend on the plane.

    The per-plane call reads both row tables, fetches the foreground rows from
    [0x177d] rightwards, and gathers the background rows - and only the
    foreground window and the column indices actually differ between planes. So
    for four planes it read four overlapping windows of the same rows and
    gathered the same background four times. Here the full row is read once and
    each plane takes a stride of it, which is free.

    Returns None when the destination is not the Mode X aperture, matching what
    the per-plane version does in that case.
    """
    g = m.dgroup_base
    u16 = lambda o: struct.unpack("<H", m.read(g + o, 2))[0]

    def farptr(o):
        off, seg = struct.unpack("<HH", m.read(g + o, 4))
        return seg * 16 + off

    width, height = u16(0x538), u16(0x53A)
    mask_x, mask_y = u16(0x1729), u16(0x172B)
    dst_lin = u16(0x16F3) * 16 + u16(0x16F1) + u16(0x1727)
    plane_off = dst_lin - 0xA0000
    if plane_off < 0 or width <= 0 or height <= 0:
        return None

    fg_rows = read_row_table(m, farptr(0x16F5), height)
    bg_rows = read_row_table(m, farptr(0x170B), mask_y + 1)
    fg_data = bulk_rows(m, fg_rows, 0, width)     # every column, once
    if not fg_data:
        return None
    fg = np.frombuffer(b"".join(fg_data), dtype=np.uint8).reshape(height, -1)

    # The background rows, gathered once. Only the distinct ones are read: the
    # wrap mask means the same few recur down the region.
    by = (np.arange(height, dtype=np.int32) +
          m.read(g + 0x177F, 1)[0]) & mask_y
    uniq, inv = np.unique(by, return_inverse=True)
    bg = np.stack([np.frombuffer(m.cached_read(bg_rows[b], mask_x + 1),
                                dtype=np.uint8) for b in uniq])[inv]
    return plane_off, width, mask_x, fg, bg


def compose_layer_plane(m, shared, plane):
    """One plane's worth of compose_layer, from the hoisted arrays."""
    plane_off, width, mask_x, fg_all, bg = shared
    fg = fg_all[:, plane::4]
    ncols = fg.shape[1]
    if ncols == 0:
        return
    idx = (np.arange(plane, width, 4, dtype=np.int32) & mask_x)[:ncols]
    out = np.where(fg == 0, bg[:, idx], fg).tobytes()
    for p in m.active_planes:
        m.planes[p][plane_off:plane_off + len(out)] = out
    m.native_pixels += len(out)


def plane_loop_layer(m):
    """The four-plane drawing loop at 0x0cd5f, done in one native call.

    The original is 57 bytes:

        for plane in 0..3:
            set_plane(plane)                 # 0x57ee
            compose_layer()                  # 0x5d3a, all arguments in DGROUP
            draw_entities(ds:0xd93, copy of ds:0x1755, colour 0)

    Everything it calls is already native, so the loop is the only part left in
    the guest - and it is the reason the compositors' fixed cost is paid four
    times per frame instead of once. The viewport is copied to the stack by value
    in the original; here its DGROUP source address is passed instead, since the
    copy is verbatim.

    This is also the step that makes flat drawing reachable: with the loop on this
    side, planar output becomes a choice made in our code rather than a shape
    imposed by the game's.
    """
    g = m.dgroup_base
    # The compositor's plane-independent work is done once here, which is the
    # point of owning the loop: the caller used to force it four times.
    shared = compose_layer_shared(m)
    for plane in range(4):
        set_plane(m, plane)
        if shared is None:
            native_compose_layer(m, 0)       # aperture not set up; let it decide
        else:
            compose_layer_plane(m, shared, plane)
        draw_entities(m, scene=g + 0x0D93, view=g + 0x1755, colour=0)


# The scene layers the scroll caller draws, in the order it draws them: six
# 12-byte records live at DGROUP:0xd63 and it takes 1, 0, 2, 3, 5, then 4 - the
# last of those (0xd93) only when its argument is zero, and it is also the only
# one the layer caller at 0x0cd5f draws. A seventh scene of its own sits at
# 0x178c behind a flag, with the other viewport.
SCROLL_LAYERS = (0x0D6F, 0x0D63, 0x0D7B, 0x0D87, 0x0D9F)


def plane_loop_scroll(m):
    """The four-plane drawing loop at 0x0e4dc, done in one native call.

    The busier of the game's two: this is the in-game frame, and it is where
    draw_entities gets most of its ~34000 calls a session.

        for plane in 0..3:
            set_plane(plane)                             # 0x57ee
            compose_scroll([0x1739], [0x173d])           # 0x5dc4
            if [0x4f6]: particles()                      # 0xab09
            draw_entities(scene, ds:0x172d, 0)           # 0xaba5, five layers
            if arg == 0:   draw_entities(ds:0xd93,  ds:0x172d, 0x90)
            if [0x178b]:   draw_entities(ds:0x178c, ds:0x1741, 0x90)
            for i in 0..2:                               # three flashing panels
                if [0x2154+i]: blit_rows_masked(...)     # 0x5ac2
            if [bp-8]:     draw_number([bp-6],   0x80, 0x22, ..., 6)
            if [bp-0xa]:   draw_number([0x2007], 0xe1, 0x22, ..., 2)
            for x in [bp-2]..[bp-4]: (*[0x53e])(x, y, 0) # a five-pixel run

    Everything it calls was already native, so the loop was the last emulated
    thing in the in-game drawing path - and the reason every fixed cost inside it
    was paid four times a frame instead of once.

    Six of the enclosing function's locals and its argument are read off the
    frame: the plane counter at [bp-0x1f] is deliberately not written back, as
    nothing after the loop reads it and both plane loops in that function
    re-initialise it at their head.
    """
    g = m.dgroup_base
    u8 = lambda a: m.read(a, 1)[0]
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    frame = m._reg(UC_X86_REG_SS) * 16 + m._reg(UC_X86_REG_BP)
    arg = u16(frame + 6)
    px0, px1 = u16(frame - 2), u16(frame - 4)
    number = s16(frame - 6)
    number_on, level_on = u16(frame - 8), u16(frame - 0x0A)

    scroll_x, scroll_y = u16(g + 0x1739), u16(g + 0x173D)
    # 16-bit and wrapping, as the original computes it.
    pixel_y = (u16(g + 0x53A) + 0xFFF9 - u16(g + 0x2003)) & 0xFFFF
    stride = plot_pixel_stride(m)
    panels = [i for i in range(3) if u8(g + 0x2154 + i)]

    # The compositor's plane-independent work, done once instead of four times -
    # which is the point of owning the loop. None means it would draw nothing, so
    # the per-plane call is skipped rather than repeated four times to find out.
    # (m.warp_calls therefore counts loops now, not calls; it exists only to say
    # whether the warp has ever run.)
    shared = compose_scroll_shared(m, scroll_x, scroll_y)

    for plane in range(4):
        set_plane(m, plane)
        if shared is not None:
            compose_scroll_plane(m, shared, plane)
        if u16(g + 0x4F6):
            native_particles(m, 0)                  # reads no arguments
        for scene in SCROLL_LAYERS:
            draw_entities(m, scene=g + scene, view=g + 0x172D, colour=0)
        if arg == 0:
            draw_entities(m, scene=g + 0x0D93, view=g + 0x172D, colour=0x90)
        if u8(g + 0x178B):
            draw_entities(m, scene=g + 0x178C, view=g + 0x1741, colour=0x90)
        for i in panels:
            blit_rows_masked(m, table=far(far(g + 0x210C + i * 4)),
                             rect=g + 0x2118 + i * 0x14, srcrow=0)
        if number_on:
            draw_number(m, number, 0x80, 0x22, g + 0x1741, 0x90, 6)
        if level_on:
            draw_number(m, u16(g + 0x2007), 0xE1, 0x22, g + 0x1741, 0x90, 2)
        if stride is not None:
            for px in range(px0, px1):
                plot_pixel(m, px, pixel_y, 0, stride)


# Loop head -> (exit address, handler). Replaced at the instruction, like the
# interrupt stubs: there is no function entry to hook, the loop is inline.

def plane_loop_hud(m):
    """The four-plane HUD loop at 0x0d9a2, done in one native call.

    Shares a function - and a plane counter at [bp-0x1f] - with the scroll loop at
    0x0e4dc. Per plane:

        set_plane(plane)                                   # 0x57ee
        blit_rows(rows from [bp-0x52], ds:0x1741, 0)       # 0x5c09, the panel
        for i in 0 .. [0x178b]-1:                          # the collected items
            draw_sprite(0x2a, 0x82 + i*16, 0x10, .., 0x90)     # the empty slot
            outline_sprite(item, same position)                # 0x65f1
            draw_sprite(item, same position, 0x90)
        draw_sprite(7,    0xd4,  0x23, .., 0x90)
        draw_sprite(0x4f, 0x105, 0x23, .., 0x90)
        draw_number2([0x2036], 6, 0x80,  0x22)             # 0xd757
        draw_number2([0x2007], 2, 0xe1,  0x22)
        draw_number2([0x2034], 2, 0x113, 0x22)
        draw_sprite(0xae, 0x135, 7, .., 0x90)

    Each item's sprite is its type - a word from the array at [0x1782] - looked up
    in the per-type sprite tables at DGROUP 0x9a, taking the first entry. This is
    the call site that exercises the outline: 8 to 16 calls a session in ordinary
    play, which is why 0x65f1 is verified even though the shadow path inside
    draw_entities has never been shown to fire.

    It runs exactly twice a level, which is worth knowing before reading a
    verification count. An outer loop over [bp-0x29] draws the whole HUD once into
    each of the two video pages, with a page flip (0x14d4b) between them, and that
    is the only time it is drawn - which is also why the loop is jumped into at
    0x0db39 rather than fallen into. The score that updates visibly every frame is
    the scroll loop's draw_number, not this one. So two comparisons in a session is
    the entire population for one level, not a thin sample of a busy loop.
    """
    g = m.dgroup_base
    u8 = lambda a: m.read(a, 1)[0]
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    frame = m._reg(UC_X86_REG_SS) * 16 + m._reg(UC_X86_REG_BP)
    rows = far(frame - 0x52)          # the panel's row table, held in a local
    items = u8(g + 0x178B)
    types = far(g + 0x1782)

    def sprite_for(i):
        t = u16(types + i * 2)
        return u16(far(g + 0x9A + t * 4))

    for plane in range(4):
        set_plane(m, plane)
        blit_rows(m, table=rows, rect=g + 0x1741, srcrow=0)
        for i in range(items):
            x = 0x82 + i * 16
            blit_sprite(m, index=0x2A, x=x, y=0x10, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
            idx = sprite_for(i)
            outline_sprite(m, index=idx, x=x, y=0x10, table=g + 0x18E9,
                           clip=g + 0x1741)
            blit_sprite(m, index=idx, x=x, y=0x10, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
        for index, x, y in ((0x07, 0x0D4, 0x23), (0x4F, 0x105, 0x23),
                            (0xAE, 0x135, 0x07)):
            blit_sprite(m, index=index, x=x, y=y, table=g + 0x18E9,
                        clip=g + 0x1741, colour=0x90)
        draw_number2(m, u16(g + 0x2036), 6, 0x080, 0x22)
        draw_number2(m, u16(g + 0x2007), 2, 0x0E1, 0x22)
        draw_number2(m, u16(g + 0x2034), 2, 0x113, 0x22)


def plane_loop_tally(m):
    """The four-plane loop at 0x0bc4b, inside the end-of-level score tally.

    The smallest of the four, and everything in it was already native:

        set_plane(plane)                                        # 0x57ee
        draw_number(SI,       0x96, [bp+8]*20 + 0x46,  6)       # 0xbb3b
        if DI: draw_number([bp-0xe], 0x96, [bp+0xa]*20 + 0x46, 6)

    The two values are in registers and locals rather than globals - SI is the
    running total being counted up, DI a flag for whether a second row is shown -
    so they are read from the frame and the CPU at the loop head, as the guest's own
    loop does on each iteration. Neither is touched inside the loop.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    frame = m._reg(UC_X86_REG_SS) * 16 + m._reg(UC_X86_REG_BP)
    first = m._reg(UC_X86_REG_SI)
    second_on = m._reg(UC_X86_REG_DI)
    y1 = (u16(frame + 8) * 0x14 + 0x46) & 0xFFFF
    y2 = (u16(frame + 0x0A) * 0x14 + 0x46) & 0xFFFF
    second = u16(frame - 0x0E)

    for plane in range(4):
        set_plane(m, plane)
        draw_number(m, first, 0x96, y1, g + 0x1769, 0, 6)
        if second_on:
            draw_number(m, second, 0x96, y2, g + 0x1769, 0, 6)


PLANE_LOOPS = {
    0x0CD5F: (0x0CD98, plane_loop_layer),
    0x0E4DC: (0x0E673, plane_loop_scroll),
    0x0D9A2: (0x0DB2C, plane_loop_hud),
    0x0BC4B: (0x0BCA9, plane_loop_tally),
}


def native_particles(m, args):
    """The particle plotter at 0x0ab09, replacing the loop rather than the pixel.

    The original walks an array of 16-byte records and plots each one through the
    function pointer at [0x53e] - plot_pixel in 320-wide mode, its 360-wide twin
    otherwise. Called once per Mode X plane, it offers every particle four times
    and plot_pixel keeps only the quarter whose x & 3 matches the selected plane.

    That is why plot_pixel had 627260 calls a session for ~157000 written bytes,
    and why replacing plot_pixel itself was the wrong level to work at: there is
    no work to batch inside it. Replacing the loop turns ~205 emulated iterations
    per plane into one pass over an array.

        [0x18c1] far ptr : the record array      [0x18cd] : how many
        rec+0  dword     : x, unsigned 1/8-pixel fixed point
        rec+4  dword     : y, same
        rec+0xc byte     : colour
        [0x1731]/[0x1733], [0x172d]/[0x172f] : viewport left/right, top/bottom
        [0x1739]/[0x173d]                    : scroll x, y

    Two details of the original's arithmetic are load-bearing. The shift helper at
    0x1148 is SHR, not SAR, so the fixed-point coordinates are unsigned. And the
    position arithmetic is 16-bit, with unsigned bounds compares - which is how
    one comparison rejects both off-left and off-right: a negative x wraps to a
    huge value and fails the upper bound. So the wrap has to be applied before
    comparing, not after.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    count = struct.unpack("<h", m.read(g + 0x18CD, 2))[0]
    if count <= 0 or not m.active_planes:
        return None
    off, seg = struct.unpack("<HH", m.read(g + 0x18C1, 4))
    # Not cached_read: unlike sprite and tile data, particles move every frame.
    raw = m.read(seg * 16 + off, count * 16)
    rec = np.frombuffer(raw, dtype=np.uint8).reshape(count, 16)
    xf = rec[:, 0:4].copy().view("<u4").ravel()
    yf = rec[:, 4:8].copy().view("<u4").ravel()

    left, right = u16(g + 0x1731), u16(g + 0x1733)
    top, bottom = u16(g + 0x172D), u16(g + 0x172F)
    x = ((xf >> 3) - u16(g + 0x1739) + left) & 0xFFFF
    y = ((yf >> 3) - u16(g + 0x173D) + top) & 0xFFFF
    keep = ((x >= left) & (x < right) & (y >= top) & (y < bottom)
            & ((x & 3) == (m.read(g + 0x177D, 1)[0] & 3)))
    if not keep.any():
        m.rows_done = count
        return None

    dst_off, dst_seg = struct.unpack("<HH", m.read(g + 0x16F1, 4))
    plane_off = dst_seg * 16 + dst_off - 0xA0000
    if plane_off < 0:
        return None
    stride = 90 if u16(g + 0x4FE) else 80
    offs = plane_off + ((y * stride + (x >> 2) + u16(g + 0x1727)) & 0xFFFF)
    offs, vals = offs[keep], rec[:, 12][keep]
    for pl in (m.planes[p] for p in m.active_planes):
        inb = offs < len(pl)
        np.frombuffer(pl, dtype=np.uint8)[offs[inb]] = vals[inb]
    m.native_pixels += int(keep.sum())
    m.rows_done = count
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


# The two single-pixel plotters, by image offset and row stride. They are the same
# routine bar the multiply, and the game keeps a far pointer to the one that
# matches the resolution in [0x53e] - which is how 0x5761 gets away with no
# [0x4fe] check inside it. So the stride comes from the pointer, not a guess.
PLOT_PIXEL_STRIDE = {0x05761: 80, 0x057A1: 90}


def plot_pixel_stride(m):
    """The row stride of whichever plotter is installed at [0x53e], or None.

    It has to be resolved as a far pointer and turned back into an image offset.
    The offset word on its own was 0x0ac1 in a live level - matching neither
    plotter, because the segment is not the one image offsets are measured from -
    and comparing that word alone silently dropped the pixel run. Which of the two
    it resolves to is not settled by the offset: both are a paragraph-aligned
    distance from 0x0ac1, so the pointer has to be followed, and the message below
    prints both halves if it ever leads somewhere else.

    The slot is 0000:0000 until a level starts, so it cannot be checked without
    playing one; see probe_plot_ptr.py.

    None means the pointer resolves to neither, which would be a hole in this
    reading of the code rather than a stride to invent - so it is said out loud
    once and the caller skips the pixels rather than writing them wrong.
    """
    off, seg = struct.unpack("<HH", m.read(m.dgroup_base + 0x53E, 4))
    target = seg * 16 + off - m.image_base
    stride = PLOT_PIXEL_STRIDE.get(target)
    if stride is None and not m.plot_pixel_warned:
        m.plot_pixel_warned = True
        known = ", ".join(f"{k:#07x}" for k in PLOT_PIXEL_STRIDE)
        print(f"  [pixel] [0x53e] is {seg:04x}:{off:04x} = image {target:#07x}, "
              f"which is neither plotter ({known}); the native plane loop is "
              f"skipping its pixel runs")
    return stride


def native_plot_pixel(m, args):
    """Stack-reading shim for the single-pixel plot at 0x05761.

        [+0x06] word : x      [+0x08] word : y      [+0x0a] byte : colour

    Stride 80 unconditionally, because that is what this one of the two does.
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    return plot_pixel(m, u16(args + 0), u16(args + 2),
                      m.read(args + 4, 1)[0], 80)


def plot_pixel(m, x, y, colour, stride):
    """One pixel, with its arguments resolved.

    Writes nothing unless x & 3 equals the current plane, so the game calls it up
    to four times per pixel. Split out of the native above so the scroll caller's
    plane loop can draw its pixel run without a dispatch per pixel.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    if m.read(g + 0x177D, 1)[0] != (x & 3):
        return None
    off, seg = struct.unpack("<HH", m.read(g + 0x16F1, 4))
    o = seg * 16 + off - 0xA0000 + y * stride + (x >> 2) + u16(g + 0x1727)
    for p in m.active_planes:
        if 0 <= o < len(m.planes[p]):
            m.planes[p][o] = colour
    m.native_pixels += 1
    return None


def native_blit_rows_masked(m, args):
    """Stack-reading shim for the masked row blitter at 0x05ac2.

        [+0x06] far ptr -> far ptr -> array of far row pointers
        [+0x0a] word    : first destination row, then last, first x, last x
        [+0x1e] word    : first source row index
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return blit_rows_masked(m, table=far(far(args + 0x00)), rect=args + 0x04,
                            srcrow=u16(args + 0x18))


def blit_rows_masked(m, table, rect, srcrow):
    """The masked row blitter at 0x05ac2, with its arguments resolved.

    Same layout as blit_rows (0x05c09), but source bytes of zero are transparent
    and leave the destination untouched. Read the existing row, overlay the
    non-zero source pixels, write it back in one go.

    `rect` is the address of the four-word destination rectangle - first and last
    row, first and last x. The guest pushes a verbatim copy of it, so the plane
    loop passes its DGROUP source address instead of copying it again.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    row0, row1 = u16(rect + 0x00), u16(rect + 0x02)
    x0, x1 = u16(rect + 0x04), u16(rect + 0x06)

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
    """Stack-reading shim for the scrolling compositor at 0x05dc4.

        [+0x06] word : x scroll
        [+0x08] word : y scroll
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    return compose_scroll(m, u16(args + 0), u16(args + 2))


def compose_scroll(m, argx, argy):
    """One plane of the scrolling compositor at 0x05dc4.

    Runs both halves of the split below, so a single call behaves exactly as it
    always did and there is one definition of the algorithm rather than a hoisted
    copy and a per-call copy. What it costs is reading the full row window instead
    of this plane's quarter of it - the right trade at one call site, since the
    only caller of 0x05dc4 in the image is the plane loop and that wants all four.
    """
    shared = compose_scroll_shared(m, argx, argy)
    if shared is None:
        return None
    compose_scroll_plane(m, shared, m.read(m.dgroup_base + 0x177D, 1)[0] & 3)
    return None


def compose_scroll_shared(m, argx, argy):
    """Everything the scrolling compositor computes that is not per-plane.

    Reading the two row tables, fetching the foreground rows and expanding the
    background rows are all independent of the selected plane, and the loop was
    paying for them four times a frame.

    Returns None when there is nothing to draw, which is what the whole call did
    in that case.

    Like compose_layer, but scrolled and with the optional background warp the
    changelog describes: when [0x2022] is set, each row's x displacement comes
    from a 32-entry table at [0x179f], stepped by [0x17c0] per row. Foreground
    pixel wins unless it is zero, in which case the wrapped background shows
    through, which is the part compose_scroll_plane does.
    """
    g = m.dgroup_base
    u8 = lambda a: m.read(a, 1)[0]
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]
    s16 = lambda a: struct.unpack("<h", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    base_x = (argx >> 1) + u8(g + 0x177E)
    row_adv = (s16(g + 0x538) - s16(g + 0x1735)) >> 2
    row0, row_end = u16(g + 0x172D), u16(g + 0x172F)
    right = u16(g + 0x1735)
    mask_x, mask_y = u16(g + 0x1729), u16(g + 0x172B)
    warp_on = u16(g + 0x2022) != 0
    phase = u8(g + 0x17BF)
    step = u8(g + 0x17C0)
    if warp_on:
        phase = (phase + ((argy >> 1) * step)) & 0xFF
        m.warp_calls += 1        # so we can tell whether this path was tested
        if m.warp_calls == 1:
            # This has never once executed, across every session so far, so the
            # code below is UNVERIFIED - written from the disassembly and never
            # compared against it. Say so the moment it does run, rather than
            # letting a wrong background go unnoticed.
            print("  [warp] the background warp is running for the first time. "
                  "This path has never been exercised and is UNVERIFIED.\n"
                  "  [warp] check it before trusting the screen:\n"
                  "  [warp]   --verify-only compose_scroll   (byte-compares "
                  "against the original body)\n"
                  "  [warp] if it mismatches, the phase sequence is the thing to "
                  "doubt: the original re-masks to 0x1f every row, so it is not "
                  "an arithmetic progression, and 0x179f/0x17bf/0x17c0 are the "
                  "table, start phase and per-row step.")

    row_bytes = 90 if u16(g + 0x4FE) else 80
    dst = (far(g + 0x16F1) - 0xA0000 + row0 * row_bytes
           + u16(g + 0x1727) + (s16(g + 0x1731) >> 2))
    nrows = max(0, row_end - row0)
    if dst < 0 or nrows == 0:
        return None

    # One read has to cover every plane, so it runs to the widest byte any of the
    # four will ask for rather than to a single plane's span.
    spans = [max(0, (right - p + 3) // 4) for p in range(4)]
    width = max([p + 4 * (s - 1) + 1 for p, s in enumerate(spans) if s] or [0])
    if width <= 0:
        return None
    fg_rows = read_row_table(m, far(g + 0x16F5), argy + nrows)
    bg_rows = read_row_table(m, far(g + 0x170B), mask_y + 1)
    fg_data = bulk_rows(m, fg_rows[argy:argy + nrows], argx, width)
    if len(fg_data) < nrows:
        return None
    fg = np.frombuffer(b"".join(fg_data), dtype=np.uint8).reshape(nrows, -1)

    # The background rows, expanded once. Only the distinct ones are read: the
    # wrap mask means the same few recur down the region.
    by = (((argy >> 1) + u8(g + 0x177F)
           + np.arange(row0, row_end, dtype=np.int32)) & mask_y)
    uniq, inv = np.unique(by, return_inverse=True)
    bg = np.stack([np.frombuffer(m.cached_read(bg_rows[b], mask_x + 1),
                                 dtype=np.uint8) for b in uniq])[inv]

    shifts = None
    if warp_on:
        # Each row takes its x displacement from a 32-entry table, stepped per
        # row. Advanced in Python because the phase is re-masked to 0x1f every
        # row, which is not a plain arithmetic progression - the reason this is not
        # simply base_x + table[(phase + r * step) & 0x1f].
        #
        # Still UNVERIFIED, for the same reason as ever: the warp has never run.
        warp = m.read(g + 0x179F, 32)
        shifts = np.empty(nrows, dtype=np.int32)
        ph = phase
        for r in range(nrows):
            ph &= 0x1F
            shifts[r] = base_x + warp[ph]
            ph = (ph + step) & 0xFF
    return dst, row_adv, right, mask_x, base_x, fg, bg, shifts, nrows


def compose_scroll_plane(m, shared, plane):
    """One plane's worth of the scrolling compositor, from the hoisted arrays.

    The plane picks its columns as a stride of the window that was read once, and
    the background displacement is applied to that selection. Foreground pixel
    wins unless it is zero, in which case the wrapped background shows through.
    """
    dst, row_adv, right, mask_x, base_x, fg_all, bg, shifts, nrows = shared
    if not m.active_planes:
        return
    span = max(0, (right - plane + 3) // 4)
    fg = fg_all[:, plane::4][:, :span]
    ncols = fg.shape[1]
    if ncols == 0:
        return
    cols = np.arange(ncols, dtype=np.int32) * 4 + plane
    if shifts is None:
        sel = bg[:, (cols + base_x) & mask_x]
    else:
        sel = np.take_along_axis(bg, (cols[None, :] + shifts[:, None]) & mask_x,
                                 axis=1)
    out = np.where(fg == 0, sel, fg)
    stride = ncols + row_adv
    if stride <= 0:
        return
    for p in m.active_planes:
        pl = m.planes[p]
        # Write the rows that fit, as the row-at-a-time version did, rather than
        # dropping the whole region when the last one runs past the plane.
        fit = min(nrows, (len(pl) - dst - ncols) // stride + 1)
        if fit <= 0:
            continue
        view = np.frombuffer(pl, dtype=np.uint8)
        np.lib.stride_tricks.as_strided(
            view[dst:], shape=(fit, ncols), strides=(stride, 1))[:] = out[:fit]
    m.native_pixels += nrows * ncols
    m.rows_done = nrows

def native_blit_rows(m, args):
    """Stack-reading shim for the row blitter at 0x05c09.

        [+0x06] far ptr -> far ptr -> array of far row pointers
        [+0x0a] word    : first destination row, then last, first x, last x
        [+0x1e] word    : first source row index
    """
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    return blit_rows(m, table=far(far(args + 0x00)), rect=args + 0x04,
                     srcrow=u16(args + 0x18))


def blit_rows(m, table, rect, srcrow):
    """The row blitter at 0x05c09, with its arguments resolved.

    No transparency test - it copies unconditionally. Source steps 4 bytes per
    pixel (staying within one plane) while the destination steps 1, so each row is
    a strided gather into a contiguous run, which Python slicing does in one go
    instead of one emulated iteration per pixel.

    `rect` addresses the four-word destination rectangle, first and last row then
    first and last x, as blit_rows_masked takes it.
    """
    g = m.dgroup_base
    u16 = lambda a: struct.unpack("<H", m.read(a, 2))[0]

    def far(a):
        off, seg = struct.unpack("<HH", m.read(a, 4))
        return seg * 16 + off

    row0, row1 = u16(rect + 0x00), u16(rect + 0x02)
    x0, x1 = u16(rect + 0x04), u16(rect + 0x06)

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
# ------------------------------------------------- one-shot interrupt stubs ---
# These fire once, in the C runtime's startup, and are inline rather than behind
# a callable function - so they cannot be replaced at a function entry like every
# other native. They are answered at the instruction instead: service in Python,
# then step IP past the two bytes of the INT.
#
# Being one-shots, they cannot be discovered adaptively the way the floating
# point sites are: the first execution is the only one. The addresses below come
# from the interrupt report of a real session rather than from a byte scan, and
# install_int_stubs() verifies the bytes at each one are the expected CD nn
# before hooking it, so a stale address is skipped loudly instead of corrupting
# execution.

def stub_dos_version(m):
    """AH=30h: report DOS 5.0, matching what the shim answers."""
    m._set(UC_X86_REG_AX, 0x0005)
    m._set(UC_X86_REG_BX, 0)


def stub_setblock(m):
    """AH=4Ah: the startup's own heap resize. Granted, as the shim grants it."""
    m._cf(False)


def stub_equipment(m):
    """INT 11h: the BIOS equipment word."""
    m._set(UC_X86_REG_AX, 0x0021)


def stub_ticks(m):
    """INT 1Ah: BIOS tick count since midnight, at 18.2 Hz.

    A real value rather than a constant on purpose. The startup reads this to
    seed the random number generator, so freezing it would make every session
    play out identically - which is a bigger behavioural change than any of the
    interrupts removed so far. The shim derived it from an interrupt counter;
    the host clock is both simpler and closer to a real machine.
    """
    now = time.localtime()
    secs = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    ticks = int(secs * 18.2065) & 0xFFFFFFFF
    m._set(UC_X86_REG_CX, (ticks >> 16) & 0xFFFF)
    m._set(UC_X86_REG_DX, ticks & 0xFFFF)
    m._set(UC_X86_REG_AX, 0)             # AL=0: no midnight rollover


def stub_get_date(m):
    """AH=2Ah: CX=year, DH=month, DL=day, AL=day of week. Real date, as above."""
    n = time.localtime()
    m._set(UC_X86_REG_CX, n.tm_year)
    m._set(UC_X86_REG_DX, (n.tm_mon << 8) | n.tm_mday)
    m._set(UC_X86_REG_AX, (n.tm_wday + 1) % 7)     # DOS counts from Sunday


def stub_get_time(m):
    """AH=2Ch: CH=hour, CL=minute, DH=second, DL=hundredths."""
    t = time.time()
    n = time.localtime(t)
    m._set(UC_X86_REG_CX, (n.tm_hour << 8) | n.tm_min)
    m._set(UC_X86_REG_DX, (n.tm_sec << 8) | int(t % 1 * 100))


def stub_dos_console_read(m):
    """AH=07h: read a character, through the shim's own console handling.

    Delegated rather than reimplemented. DOS delivers extended keys as two reads
    - a 0x00 prefix, then the scancode - and that protocol lives in
    emulation.py._dos(); it is the reason the arrow keys work at all, and a
    second copy of it here would be a second thing to get wrong. The gain is the
    interrupt, not the servicing.

    Unlike the rest of these, this site is not a one-shot: it fires once per
    character read, 88 times in a played session.
    """
    m._dos()


def stub_dos_truncate(m):
    """AH=40h raised by 0x0397d, the runtime's truncate helper.

    A near-call "ret 2" Pascal-convention function - the callee pops its own
    arguments - which is what put it out of reach of the entry-replacement
    mechanism. Its interrupt is still reachable here. It sets CX=DX=0 to truncate
    at the current position, which is why that semantic belongs in the shim
    rather than in the write native: this caller never goes through 0x04b10.
    """
    m._dos()


def stub_bios_video(m):
    """INT 10h raised inside the runtime's own video wrapper, for AH=0Fh.

    The wrapper's mode-query path is deliberately left to run - the native
    declines it, because it chains BIOS calls and reads BIOS variables - but its
    interrupt can still be answered here. Delegating reproduces the shim exactly,
    including that AL keeps whatever it already held, which the wrapper then
    compares against 3. Faithful rather than improved: supplying a "better" value
    would change a decision the game has always made on that one.
    """
    m._bios_video()


def stub_get_vector(m):
    """AH=35h: hand back the interrupt vector from the table, in ES:BX."""
    al = m._reg(UC_X86_REG_AX) & 0xFF
    off, seg = struct.unpack("<HH", m.uc.mem_read(al * 4, 4))
    m._set(UC_X86_REG_BX, off)
    m.uc.reg_write(UC_X86_REG_ES, seg)


def stub_set_vector(m):
    """AH=25h: install DS:DX as the handler for interrupt AL.

    Most of these install Borland's floating-point handlers, which --native-fp
    has made unreachable - but they are still written, because the vectors must
    read back correctly for the runtime's own save-and-restore on exit.
    """
    al = m._reg(UC_X86_REG_AX) & 0xFF
    ds, dx = m._reg(UC_X86_REG_DS), m._reg(UC_X86_REG_DX)
    m.uc.mem_write(al * 4, struct.pack("<HH", dx, ds))
    m.hooked_vectors[al] = (ds, dx)


# Which interrupts can be answered outright, keyed by (interrupt, AH). AH is read
# from the register at the site, exactly as the interrupt handler would, so one
# address serving different functions on different runs still dispatches right.
INT_STUBS = {
    (0x21, 0x30): ("dos_version", stub_dos_version),
    (0x21, 0x4A): ("setblock_startup", stub_setblock),
    (0x21, 0x35): ("get_vector", stub_get_vector),
    (0x21, 0x25): ("set_vector", stub_set_vector),
    (0x21, 0x2A): ("get_date", stub_get_date),
    (0x21, 0x2C): ("get_time", stub_get_time),
    (0x21, 0x07): ("console_read", stub_dos_console_read),
    (0x21, 0x40): ("dos_truncate", stub_dos_truncate),
    (0x10, 0x0F): ("bios_mode_query", stub_bios_video),
    (0x1A, None): ("bios_ticks", stub_ticks),
    (0x11, None): ("bios_equipment", stub_equipment),
}
# Interrupt sites answered at the instruction, as image offsets. Mostly the
# startup's one-shots. Recorded
# from a real session's interrupt report and verified byte-for-byte at install,
# so a wrong address is skipped rather than silently stepped over. Interrupt
# numbers only; which function each site asks for is read from AH on arrival.
STUBBED_INT_SITES = [
    (0x0000A, 0x21),        # DOS version, the runtime's first act
    (0x00094, 0x21),        # AH=4Ah, shrink the startup memory block
    (0x0010B, 0x1A),        # BIOS tick count, seeds the RNG
    (0x0036D, 0x11),        # BIOS equipment word
    (0x01015, 0x21),        # AH=2Ah get date
    (0x01028, 0x21),        # AH=2Ch get time
    # AH=35h/25h: save the vectors the runtime replaces, then install its own.
    # Most of these are the floating-point handlers that --native-fp leaves
    # unreachable, but they must still read back correctly for the restore on
    # exit.
    (0x0017E, 0x21), (0x0018B, 0x21), (0x00198, 0x21), (0x001A5, 0x21),
    (0x001B9, 0x21), (0x002F5, 0x21), (0x00305, 0x21), (0x00312, 0x21),
    (0x00415, 0x21), (0x00420, 0x21), (0x0042A, 0x21), (0x00F32, 0x21),
    (0x00F45, 0x21), (0x01051, 0x21),
    # Not a one-shot: getch's character read, 88 calls in a played session. Its
    # interrupt is 0xa2 bytes inside 0x02786, past arithmetic on [0x8b]/[0x8d],
    # so there is no wrapper entry to replace - which is exactly what answering
    # at the instruction is for.
    (0x02828, 0x21),
    (0x03989, 0x21),        # the truncate helper's write, Pascal convention
    (0x020D0, 0x10),        # AH=0Fh mode query, inside the declined wrapper path
]


def _errno(m, dos_err):
    """Reproduce the runtime's DOS-error-to-errno mapping, function 0x011ed.

    Without this, a native that hits a failure has to decline and let the
    original body raise the interrupt after all - and for the save-slot scan
    failure is the common case, since four of five slots are usually empty. The
    helper stores the DOS code in _doserrno at DGROUP:0x2f9c, translates it
    through the byte table at 0x2f9e, stores that in errno at DGROUP:0x7f, and
    returns 0xFFFF, which is also what its callers return.
    """
    if dos_err > 0x58:
        dos_err = 0x57
    m.uc.mem_write(m.dgroup_base + 0x2F9C, struct.pack("<H", dos_err))
    e = m.uc.mem_read(m.dgroup_base + 0x2F9E + dos_err, 1)[0]
    m.uc.mem_write(m.dgroup_base + 0x007F, struct.pack("<H", e))
    return 0xFFFF


def _dos_via_shim(m, ax, **regs):
    """Run one DOS function through the shim and report whether it failed.

    The natives below deliberately do not reimplement what trace_dos.py already
    does - handle allocation, the save overlay, write-back on close. Duplicating
    that logic is how the two drift apart, and its last bug was subtle enough to
    cost a debugging session. So they set up the registers the interrupt would
    have carried and call the same servicing code.
    """
    m._set(UC_X86_REG_AX, ax)
    for name, val in regs.items():
        m._set({"bx": UC_X86_REG_BX, "cx": UC_X86_REG_CX,
                "dx": UC_X86_REG_DX}[name], val)
    m._dos()
    failed = bool(m.uc.reg_read(UC_X86_REG_EFLAGS) & 1)
    return failed, m._reg(UC_X86_REG_AX)


def native_dos_close(m, args):
    """close(handle): INT 21h AH=3Eh, then clear the runtime's flags entry.

    The original returns 0 on success and 0xFFFF with errno set on failure, and
    on success zeroes this handle's word in the file-flags table at
    DGROUP:0x2f6e - the same table write() consults to reject a read-only
    handle, so it has to be maintained.
    """
    h = struct.unpack("<H", m.uc.mem_read(args, 2))[0]
    if h not in m.handles:
        return _errno(m, 6)                      # invalid handle
    _dos_via_shim(m, 0x3E00, bx=h)
    m.uc.mem_write(m.dgroup_base + 0x2F6E + 2 * h, b"\x00\x00")
    return 0


def native_dos_getattr(m, args):
    """_dos_getfileattr(path, func, attr): INT 21h AH=43h.

    Only the query direction (func 0) is served; setting attributes declines,
    having never been observed. On success the original returns the attribute
    word from CX, which is why it ends in "xchg cx,ax".
    """
    func = struct.unpack("<H", m.uc.mem_read(args + 4, 2))[0] & 0xFF
    if func != 0:
        return DECLINE
    off, seg = struct.unpack("<HH", m.uc.mem_read(args, 4))
    ds = m._reg(UC_X86_REG_DS)
    m.uc.reg_write(UC_X86_REG_DS, seg)
    failed, ax = _dos_via_shim(m, 0x4300, dx=off)
    m.uc.reg_write(UC_X86_REG_DS, ds)
    if failed:
        return _errno(m, ax)
    return m._reg(UC_X86_REG_CX)


# The runtime's per-handle flags, one word each, indexed by file descriptor.
# open() fills a slot, write() consults and updates it, close() clears it - so a
# native replacing any one of them has to maintain it or the others misbehave.
FLAGS_TABLE = 0x2F6E        # DGROUP offset of the table
FLAGS_COUNT = 0x2F6C        # DGROUP offset of its length


def native_dos_open(m, args):
    """_open(path, oflags): INT 21h AH=3Dh, then record the handle's flags.

    The access mode DOS wants is derived from the O_ bits - bit 1 write-only,
    bit 2 read/write, neither read-only - with the sharing bits in 0xf0 passed
    straight through. On success the original stores (oflags & 0xb8ff) | 0x8000
    in this handle's flags word, which is what write() later tests to reject a
    read-only handle, and returns the descriptor.
    """
    off, seg = struct.unpack("<HH", m.uc.mem_read(args, 4))
    flags = struct.unpack("<H", m.uc.mem_read(args + 4, 2))[0]
    mode = 1 if flags & 2 else (2 if flags & 4 else 0)
    mode |= flags & 0xF0
    ds = m._reg(UC_X86_REG_DS)
    m.uc.reg_write(UC_X86_REG_DS, seg)
    failed, ax = _dos_via_shim(m, 0x3D00 | mode, dx=off)
    m.uc.reg_write(UC_X86_REG_DS, ds)
    if failed:
        return _errno(m, ax)
    fd = ax
    limit = struct.unpack("<H", m.uc.mem_read(m.dgroup_base + FLAGS_COUNT, 2))[0]
    if fd >= limit:
        # The original writes regardless and would corrupt whatever follows the
        # table. Our handle allocator caps below the limit so this cannot happen;
        # say so rather than silently scribbling if that ever changes.
        print(f"  [file] handle {fd} is past the {limit}-entry flags table")
        return fd
    m.uc.mem_write(m.dgroup_base + FLAGS_TABLE + 2 * fd,
                   struct.pack("<H", (flags & 0xB8FF) | 0x8000))
    return fd


def native_dos_write(m, args):
    """_write(handle, buf, count): INT 21h AH=40h, guarded by the flags table.

    A handle opened read-only - bit 0 of its flags word - is refused with DOS
    error 5 before any call is made, exactly as the original does. On success the
    original sets bit 0x1000 to mark the handle written to.
    """
    h = struct.unpack("<H", m.uc.mem_read(args, 2))[0]
    off, seg = struct.unpack("<HH", m.uc.mem_read(args + 2, 4))
    count = struct.unpack("<H", m.uc.mem_read(args + 6, 2))[0]
    slot = m.dgroup_base + FLAGS_TABLE + 2 * h
    flags = struct.unpack("<H", m.uc.mem_read(slot, 2))[0]
    if flags & 1:
        # Worth announcing: a wrong flags-table address would refuse every write
        # here while the original happily performed it, and the failure would
        # otherwise be invisible - the runtime just gets EACCES back.
        m._fop(f"WRITE REFUSED handle {h}, flags {flags:#06x} says read-only")
        return _errno(m, 5)                     # access denied
    ds = m._reg(UC_X86_REG_DS)
    m.uc.reg_write(UC_X86_REG_DS, seg)
    failed, ax = _dos_via_shim(m, 0x4000, bx=h, cx=count, dx=off)
    m.uc.reg_write(UC_X86_REG_DS, ds)
    if failed:
        return _errno(m, ax)
    m.uc.mem_write(slot, struct.pack("<H", flags | 0x1000))
    return ax


# Which interrupts int86 may raise that we can service without building code.
# Deliberately a whitelist: anything else declines and lets the original stub run,
# because these handlers are the ones we know read only the general registers.
# An interrupt wanting ES:BP (a font pointer, say) would need the segment struct
# threading through as well.
_INT86_NATIVE = {
    0x33: lambda m: m._mouse(),
    0x10: lambda m: m._bios_video(),
}
_INT86_REGS = (UC_X86_REG_AX, UC_X86_REG_BX, UC_X86_REG_CX, UC_X86_REG_DX,
               UC_X86_REG_SI, UC_X86_REG_DI)


def native_int86(m, args):
    """The worker behind int86/int86x, at 0x293a.

    It exists because x86 has no INT with a register operand, so the runtime
    assembles one: "push bp; int nn; pop bp; retf" - the bytes 55 CD nn 5D CB -
    into its own stack frame and calls it. That is why the last interrupts in a
    session came from an address outside the image with nothing to hook: the
    instruction is on the stack, written moments before it executes.

    Replacing the function that builds it is the right level to work at. The
    arguments are the interrupt number and far pointers to the input, output and
    segment register structs, each of the latter being ax, bx, cx, dx, si, di,
    cflag, flags.

    SI and DI are restored afterwards because the original saves them; AX to DX
    are left as the interrupt returned them, also as the original does.
    """
    intno = struct.unpack("<H", m.uc.mem_read(args, 2))[0] & 0xFF
    handler = _INT86_NATIVE.get(intno)
    if handler is None:
        return DECLINE
    in_off, in_seg, out_off, out_seg = struct.unpack("<4H",
                                                    m.uc.mem_read(args + 2, 8))
    regs = struct.unpack("<8H", m.uc.mem_read(in_seg * 16 + in_off, 16))
    keep_si, keep_di = m._reg(UC_X86_REG_SI), m._reg(UC_X86_REG_DI)
    for reg, val in zip(_INT86_REGS, regs):
        m._set(reg, val)
    handler(m)
    out = [m._reg(r) for r in _INT86_REGS]
    flags = m.uc.reg_read(UC_X86_REG_EFLAGS) & 0xFFFF
    m.uc.mem_write(out_seg * 16 + out_off,
                   struct.pack("<8H", *out, flags & 1, flags))
    m._set(UC_X86_REG_SI, keep_si)
    m._set(UC_X86_REG_DI, keep_di)
    return out[0]


def _device_info(handle):
    """The device-information word DOS returns for AH=44h AL=00h.

    Bit 7 marks a character device. The shim ignores AH=44h entirely, so today
    the game reads whatever happened to be in DX - it works only because the
    answer is used to pick stream buffering. Answering properly is both
    deterministic and correct: the three standard handles are the console, and
    everything the game opens is a file.
    """
    return 0x80 if handle in (0, 1, 2) else 0x00


def native_isatty(m, args):
    """isatty(handle): AH=44h AL=00h, returning the device bit from DX."""
    h = struct.unpack("<H", m.uc.mem_read(args, 2))[0]
    return _device_info(h) & 0x80


def native_ioctl(m, args):
    """ioctl(handle, func, ...): AH=44h. Only get-device-info is served."""
    h = struct.unpack("<H", m.uc.mem_read(args, 2))[0]
    func = struct.unpack("<H", m.uc.mem_read(args + 2, 2))[0] & 0xFF
    if func != 0:
        return DECLINE
    return _device_info(h)


def native_dos_setblock(m, args):
    """Borland's _dos_setblock: resize the DOS memory block the heap lives in.

    INT 21h AH=4Ah with ES=segment, BX=new size in paragraphs; on success the
    original returns 0xFFFF, on failure it routes DOS's error code through the
    runtime's errno helper. Our DOS never fails this call - it grants whatever is
    asked - so success is the only reachable answer, and 799 interrupts in a
    session collapse to a constant.
    """
    return 0xFFFF


def native_bios_video(m, args):
    """Borland's INT 10h wrapper, which takes its function number in AH.

    The wrapper exists to work around video-BIOS quirks: it special-cases mode
    setting (AH=00h) and mode query (AH=0Fh), and passes everything else
    straight through. Those two paths read and write BIOS data-area variables
    and chain several INT 10h calls, so they are declined and left to run.

    Everything else - 990 get-cursor and 496 set-cursor calls in one session -
    is delegated to the same handler the interrupt would have reached. That
    handler is NOT a no-op, which an earlier version of this native assumed after
    reading only trace_dos.py: emulation.py overrides it to track the cursor for
    real, because Ducks calls AH=03h to find out where to write and then pokes
    0xb8000 itself. Returning without setting DX made every message compute row 0
    and overwrite the last one - a bug that had already been found and fixed once
    before this native briefly reintroduced it.
    """
    ah = (m._reg(UC_X86_REG_AX) >> 8) & 0xFF
    if ah in (0x00, 0x0F):
        return DECLINE
    m._bios_video()
    return None


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
    (0x0BB3B, "draw_number", native_draw_number, "far"),
    (0x0D757, "draw_number2", native_draw_number2, "far"),
    (0x05C09, "blit_rows", native_blit_rows, "far"),
    (0x05DC4, "compose_scroll", native_compose_scroll, "far"),
    (0x05AC2, "blit_rows_masked", native_blit_rows_masked, "far"),
    (0x05761, "plot_pixel", native_plot_pixel, "far"),
    (0x04D2A, "clear_vram", native_clear_vram, "far"),
    (0x0ABA5, "draw_entities", native_draw_entities, "far"),
    (0x065F1, "outline_sprite", native_outline_sprite, "far"),
    (0x0AB09, "particles", native_particles, "far"),
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
# Enabled with --native-setup. Runtime helpers that raise interrupts for
# services our machine either grants unconditionally or ignores outright.
SETUP_NATIVES = [
    (0x02E07, "dos_setblock", native_dos_setblock, "far"),
    (0x02067, "bios_video", native_bios_video, "near"),
    (0x0293A, "int86", native_int86, "far"),
]

XMS_NATIVES = [
    (0x159AE, "xms_present", native_xms_present, "far"),
    (0x159C7, "xms_get_entry", native_xms_get_entry, "far"),
]

FILE_NATIVES = [
    (0x014A3, "dos_read", native_dos_read, "far"),
    (0x012EB, "dos_lseek", native_dos_lseek, "far"),
    # The DOS layer underneath the native fopen/fread. Thin wrappers: set up
    # registers, one INT 21h, map the result, route failures through the errno
    # helper at 0x011ed - which _errno() reproduces so a failure need not fall
    # back to the interrupt.
    (0x02F72, "dos_close", native_dos_close, "far"),
    (0x02F2D, "dos_getattr", native_dos_getattr, "far"),
    (0x01238, "isatty", native_isatty, "far"),
    (0x029D3, "ioctl", native_ioctl, "far"),
    (0x03AFE, "dos_open", native_dos_open, "far"),
    (0x04B10, "dos_write", native_dos_write, "far"),
    # creat (0x03962) is left emulated: it ends in "ret 6", the Pascal
    # convention where the callee pops its own arguments, which the native
    # dispatcher does not model. One call per session.
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
    ap = argparse.ArgumentParser(
        description="Run Ducks with the native port. Everything this port "
                    "replaces is on by default; each piece can be turned off "
                    "with its --no- form, which is how to check whether a "
                    "native is responsible for something.")
    ap.add_argument("--exe", default="./Ducks.unpacked.exe")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--chunk", type=int, default=400_000)
    ap.add_argument("--blaster", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="emulate the Sound Blaster")
    ap.add_argument("--profile", action="store_true",
                    help="report which routines do the drawing, then exit")
    ap.add_argument("--profile-sound", action="store_true",
                    help="profile writes to the sound DMA buffer from the start")
    ap.add_argument("--native-file", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="serve the raw DOS read/lseek wrappers natively")
    ap.add_argument("--native-keyboard", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="serve kbhit() natively, removing all key polling "
                         "through DOS")
    ap.add_argument("--native-mouse", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="serve the game's mouse wrappers natively, removing "
                         "all INT 33h traffic")
    ap.add_argument("--trace-file", action="store_true",
                    help="record which functions do file I/O, and how much")
    ap.add_argument("--trace-keyboard", action="store_true",
                    help="record which game functions poll the keyboard")
    ap.add_argument("--trace-mouse", action="store_true",
                    help="record which game functions poll INT 33h")
    ap.add_argument("--sound-bank", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="capture samples into an indexed bank as they load")
    ap.add_argument("--native-sound", default=True,
                    action=argparse.BooleanOptionalAction,
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
    ap.add_argument("--native-xms", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="service XMS with no interrupts: driver entry as a "
                         "code hook, plus the two INT 2Fh detection sites")
    ap.add_argument("--native-setup", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="serve the C runtime's heap-resize, INT 10h wrapper "
                         "and one-shot startup interrupts natively")
    ap.add_argument("--skip-natives", default="",
                    help="comma-separated natives to leave emulated, to measure "
                         "whether replacing them actually helps")
    ap.add_argument("--verify-only", default="",
                    help="comma-separated natives to verify even if skipped")
    ap.add_argument("--native-plane-loop", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="replace the guest's four four-plane drawing loops "
                         "natively")
    ap.add_argument("--native-fp", default=True,
                    action=argparse.BooleanOptionalAction,
                    help="put Borland's emulated x87 instructions back and "
                         "let the real FPU run them")
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
               native_setup=args.native_setup,
               skip_natives={n.strip() for n in args.skip_natives.split(",")
                             if n.strip()},
               persist=not args.read_only,
               max_insns=1 << 62)
    m.voices = NativeVoices(m, bank=m.bank) if args.native_sound else None
    if args.native_sound or args.sound_bank:
        m.capture_loader()
    if args.native_setup:
        m.install_int_stubs()
    if args.native_xms:
        m.install_native_xms()
    if args.native_fp:
        m.install_native_fp()
    if args.native_plane_loop:
        m.install_plane_loops()
    if args.verify_only:
        m.verify_only = {n.strip() for n in args.verify_only.split(",") if n.strip()}
        m.verify = True
        print(f"  [verify] checking only {sorted(m.verify_only)}")
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
    m.native_time_report()
    m.fp_report(img)
    m.int_report(img)
    m.file_report(img)
    if m.native_file:
        print(f"  native file I/O : {m.file_reads} reads ({m.file_bytes} bytes), "
              f"{m.file_seeks} seeks, {m.native_declined} declined")
    if m.file_ops:
        print(f"  file operations ({len(m.file_ops)}):")
        for op in m.file_ops[-40:]:
            print(f"    {op}")
    if m.stdout:
        print("  program console output:")
        for line in m.stdout.decode("latin1").replace("\r\n", "\n").split("\n"):
            print(f"    | {line}")
    if m.files_persisted:
        print(f"  saved to disk   : {m.files_persisted}")
    elif m.overlay:
        print(f"  overlay files   : "
              f"{ {k: len(v) for k, v in m.overlay.items()} } (not persisted)")
    pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
