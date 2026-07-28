"""Capture and restore the whole machine, so a state reached by playing can be
replayed by a test.

The problem this solves: verifying a native needs the game to be *in* the state
that calls it. The in-game frame loop only runs while a level is being played,
the HUD loop runs twice per level and never again, and the end-of-level tally
loop needs a level finished. Reaching those by hand, every time, is what made
verification expensive - and a 15-minute session that never left the text screen
recorded zero comparisons at all.

So: play once, capture, and from then on start there.

What is captured
----------------
Everything the machine's behaviour depends on:

- the 2 MB of guest memory, which is one flat Unicorn mapping, so it is one read
- the CPU registers, including the segment registers and the flags word
- the four VGA planes and the register state around them: map mask, active
  planes, CRTC file, start address and its addressing unit, palette, mode
- the input state the game polls: key buffer, pending extended-key half, mouse
  position, button mask and the per-button press/release counts
- XMS: every extended-memory block, with the free counter and the handle table.
  The game's samples live here, so a snapshot without them has no sound
- the Sound Blaster model's DSP and DMA state
- the DOS shim's open files - position, and for anything the game has written to,
  the bytes - plus the overlay of files it created
- the sample bank, so sound indices still mean the same thing after a restore

What is deliberately not captured
---------------------------------
**Hooks and natives are not state.** They are installed by `build_machine()`
from the flags, which is what lets a snapshot taken with everything on be
replayed with one piece turned off - the whole point of the `--no-` forms.

**Playing voices are not resumed.** Restoring into the middle of a sample would
mean reconstructing pygame channel cursors for no benefit; the drawing path is
what tests check. Voices are stopped on restore and the game starts new ones.

**The clock is carried as elapsed time, not as a timestamp.** `t0` is a host
`perf_counter()` reading and restoring it verbatim would be meaningless, but leaving
it at a fresh machine's value is worse: the PIT counter (ports 0x40-0x42) and the
vertical retrace bit (0x3DA) are both derived from `_elapsed()`, and the game paces
itself on them. A restored machine whose clock starts near zero looks to the game
like time ran backwards - an enormous unsigned delta - and it responds by skipping
frames to catch up. Measured before the fix: the first ten display frames ran 24-36
plane-loop calls each against a steady-state 9.7, taking 600 ms instead of 138 ms,
converging after about 20 frames.

So the manifest carries `elapsed`, and restore sets `t0 = now - elapsed`. That keeps
the PIT and the retrace phase continuous across a save and load. `sb_last_tick` is
reset rather than restored, so the sound service re-bases instead of charging the
whole gap to one interval.

**The floating-point site table is not captured, and does not need to be.** Sites
patch themselves on first execution by overwriting two bytes in the image, and
those bytes live in guest memory - so they restore with it. A restored machine
sees an empty site cache and never consults it, because a patched site raises no
interrupt.

Capture at a frame boundary, not anywhere
-----------------------------------------
The capture point is the **top of the page flip**, before it swaps the pages: the
game has finished drawing and asked to show the result, so a restore resumes at the
flip's entry and flips exactly once, as it would have. The display loop's boundary
between emulation slices is kept as a fallback, for `--no-native-flip` and for
states that never flip, such as a text screen.

Two points that were tried and rejected: after the swap inside the flip, where a
restore swaps a second time; and the display loop alone, which is exact but under
70 Hz pacing iterates only ~1.3 times a second, so a keypress lands up to 0.8s late
- too coarse to catch a moment on screen, as an attempt to capture the end of a
score tally showed.

Either way it is a frame boundary, and that matters for two reasons, both learned
the hard way elsewhere in this project:

- The x87 stack is empty there. Borland's FP sites now run on the real FPU, and
  Unicorn's x87 register file is not reliably readable; capturing mid-expression
  would silently lose intermediate values. The tag word is checked at capture and
  a warning is printed if the stack is not empty, rather than writing a snapshot
  that looks fine and restores wrong.
- A native handler part-way through reading its arguments off the stack has state
  in Python locals, not in the machine. At a frame boundary there is none.

The file format
---------------
A zip: `manifest.json` plus raw binary members. Deliberately not a pickle - a
snapshot is meant to still load after this code changes, and to be inspectable
with `unzip -l`. The image's SHA-256 is recorded and checked, because restoring a
snapshot onto a differently-unpacked image would be silently wrong rather than
loudly broken.

**Snapshots contain the game's own decompressed code and data.** They are as
copyrighted as the executable they came from: `snapshots/` and `*.snap` are
git-ignored, and nothing here writes outside that directory by default.
"""

import base64
import hashlib
import json
import os
import struct
import time
import zipfile
from collections import Counter, deque

from unicorn.x86_const import (
    UC_X86_REG_CS, UC_X86_REG_DS, UC_X86_REG_EAX, UC_X86_REG_EBP,
    UC_X86_REG_EBX, UC_X86_REG_ECX, UC_X86_REG_EDI, UC_X86_REG_EDX,
    UC_X86_REG_EFLAGS, UC_X86_REG_EIP, UC_X86_REG_ESI, UC_X86_REG_ESP,
    UC_X86_REG_ES, UC_X86_REG_FS, UC_X86_REG_GS, UC_X86_REG_SS,
)

import nsound
from trace_dos import MEM_SIZE, Handle, host_path

SNAP_VERSION = 1
SNAP_DIR = "snapshots"

# The whole register file, by the 32-bit names: Unicorn tracks full-width
# registers even in real mode, so reading AX would quietly drop the high half of
# anything the game left there.
REGS = {
    "eax": UC_X86_REG_EAX, "ebx": UC_X86_REG_EBX, "ecx": UC_X86_REG_ECX,
    "edx": UC_X86_REG_EDX, "esi": UC_X86_REG_ESI, "edi": UC_X86_REG_EDI,
    "ebp": UC_X86_REG_EBP, "esp": UC_X86_REG_ESP, "eip": UC_X86_REG_EIP,
    "eflags": UC_X86_REG_EFLAGS,
    "cs": UC_X86_REG_CS, "ds": UC_X86_REG_DS, "es": UC_X86_REG_ES,
    "ss": UC_X86_REG_SS, "fs": UC_X86_REG_FS, "gs": UC_X86_REG_GS,
}

# Read if the build exposes them; a missing constant is not an error, it just
# means this Unicorn cannot report that piece and the check below is skipped.
FP_REGS = {}
for _name in ("FPCW", "FPSW", "FPTAG"):
    _const = getattr(__import__("unicorn.x86_const", fromlist=[""]),
                     f"UC_X86_REG_{_name}", None)
    if _const is not None:
        FP_REGS[_name.lower()] = _const

# Plain per-subsystem attribute lists. Explicit rather than "everything in
# __dict__" so that adding an attribute to a class does not silently start
# appearing in snapshots - or silently fail to.
VGA_ATTRS = [
    # chain4 decides how the screen is *read*: linear aperture, or the four
    # planes interleaved. A fresh machine starts chained and the game unchains it
    # on the way into Mode X, so omitting this restores a state that draws
    # correctly and displays black - which is how it was found, by looking at it.
    "chain4",
    "palette", "dac_index", "dac_phase", "dac_latch", "seq_index",
    "map_mask", "active_planes", "crtc", "crtc_index", "start_addr",
    "crtc_offset", "start_mult", "mode", "width", "height",
    "text_mode", "cursor", "active_page", "pit_latch_toggle", "pit_initial",
    "palette_writes",
]
INPUT_ATTRS = [
    "key_buf", "pending_scan", "last_scancode", "mouse_pos", "mouse_btn",
    "mouse_rel", "mouse_sens", "press_count", "release_count", "press_pos",
    "release_pos", "mouse_x", "mouse_y",
]
DOS_ATTRS = [
    "next_handle", "dta", "finished", "video_modes", "hooked_vectors",
    "load_seg", "start",
]
XMS_ATTRS = ["total_kb", "free_kb", "next_handle", "locks"]


# --------------------------------------------------------------- encoding
# JSON cannot hold tuples, deques, byte strings, Counters or integer dict keys,
# and all five appear in the state above. Tag them on the way out and undo it on
# the way in, rather than flattening and losing the distinction: active_planes
# has to come back a tuple, and crtc has to come back keyed by int.

def _enc(v):
    # bytearray and bytes are tagged apart deliberately. The card model appends
    # to sb.pcm and sb.direct_pcm as it runs, so handing those back immutable
    # restores a machine that dies on the next sound service - which is exactly
    # what happened when both encoded to the same tag.
    if isinstance(v, bytearray):
        return {"__t": "ba", "v": base64.b64encode(bytes(v)).decode()}
    if isinstance(v, bytes):
        return {"__t": "b", "v": base64.b64encode(v).decode()}
    if isinstance(v, tuple):
        return {"__t": "t", "v": [_enc(x) for x in v]}
    if isinstance(v, deque):
        return {"__t": "q", "v": [_enc(x) for x in v]}
    if isinstance(v, Counter):
        return {"__t": "c", "v": [[_enc(k), _enc(x)] for k, x in v.items()]}
    if isinstance(v, dict):
        return {"__t": "d", "v": [[_enc(k), _enc(x)] for k, x in v.items()]}
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    raise TypeError(f"snapshot: cannot encode {type(v).__name__}")


def _dec(v):
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if isinstance(v, dict):
        t = v.get("__t")
        if t == "b":
            return base64.b64decode(v["v"])
        if t == "ba":
            return bytearray(base64.b64decode(v["v"]))
        if t == "t":
            return tuple(_dec(x) for x in v["v"])
        if t == "q":
            return deque(_dec(x) for x in v["v"])
        if t == "c":
            return Counter({_dec(k): _dec(x) for k, x in v["v"]})
        if t == "d":
            return {_dec(k): _dec(x) for k, x in v["v"]}
        raise ValueError(f"snapshot: unknown tag {t!r}")
    return v


def _grab(obj, names):
    return {n: _enc(getattr(obj, n)) for n in names if hasattr(obj, n)}


def _coerce(old, new):
    """Give `new` the type of the `old` value it replaces.

    The freshly built machine is the schema: its attributes already have the
    types the code expects. `sb.pcm` is a bytearray the card model appends to as
    it runs, and restoring it as immutable bytes kills the next sound service -
    which is how this was found. Coercing here rather than trusting the tag also
    means snapshots written before bytes and bytearray were tagged apart still
    load correctly, instead of being invalidated by the fix.
    """
    if isinstance(old, bytearray) and isinstance(new, (bytes, bytearray)):
        return bytearray(new)
    if isinstance(old, tuple) and isinstance(new, (list, tuple)):
        return tuple(new)
    if isinstance(old, deque) and isinstance(new, (list, deque)):
        return deque(new)
    if isinstance(old, Counter) and isinstance(new, dict):
        return Counter(new)
    return new


def _put(obj, names, state):
    for n in names:
        if n in state:
            setattr(obj, n, _coerce(getattr(obj, n, None), _dec(state[n])))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- capture

def capture(m, note=""):
    """Freeze the machine. Returns (manifest dict, {member name: bytes})."""
    blobs = {}
    warnings = []

    blobs["mem.bin"] = bytes(m.uc.mem_read(0, MEM_SIZE))
    for i, p in enumerate(m.planes):
        blobs[f"plane{i}.bin"] = bytes(p)

    regs = {n: m.uc.reg_read(c) for n, c in REGS.items()}
    fp = {}
    for n, c in FP_REGS.items():
        try:
            fp[n] = m.uc.reg_read(c)
        except Exception:
            pass
    # 0xffff is "all eight registers empty". Anything else means we are inside
    # an x87 expression, which is not a state this can carry.
    if fp.get("fptag", 0xFFFF) != 0xFFFF:
        warnings.append(f"x87 stack not empty (tag={fp['fptag']:#06x}) - "
                        f"capture at a frame boundary, not mid-expression")

    files = {}
    for hn, h in sorted(m.handles.items()):
        key = getattr(h, "key", None)
        # A handle the game has written to, or one backed by the overlay rather
        # than a real file, is authoritative in memory: store the bytes. A
        # read-only host file is re-read on restore instead, which keeps the 2.4
        # MB Main.egg out of every snapshot.
        own = bool(key) or bool(getattr(h, "writable", False)) \
            or bool(getattr(h, "written", 0))
        ent = {"path": h.path, "pos": h.pos,
               "writable": getattr(h, "writable", False),
               "written": getattr(h, "written", 0),
               "key": key, "size": len(h.data), "sha": _sha(bytes(h.data)),
               "own": own}
        if own:
            member = f"files/h{hn}.bin"
            blobs[member] = bytes(h.data)
            ent["member"] = member
        files[str(hn)] = ent

    overlay = {}
    for i, (name, data) in enumerate(sorted(m.overlay.items())):
        member = f"overlay/{i}.bin"
        blobs[member] = bytes(data)
        overlay[name] = member

    xms = {"attrs": _grab(m.xms, XMS_ATTRS), "handles": {}}
    for h, block in sorted(m.xms.handles.items()):
        member = f"xms/{h}.bin"
        blobs[member] = bytes(block)
        xms["handles"][str(h)] = {"member": member, "size": len(block)}

    sb = None
    if getattr(m, "sb", None) is not None:
        sb = {}
        for k, v in vars(m.sb).items():
            if k.startswith("_"):
                continue
            try:
                sb[k] = _enc(v)
            except TypeError:
                pass          # a callable or a model object: not state

    bank = None
    if getattr(m, "bank", None) is not None:
        bank = {"entries": [], "plays": _enc(getattr(m.bank, "plays", {}))}
        for i, e in enumerate(m.bank.entries):
            ent = {k: _enc(v) for k, v in e.items() if k != "pcm"}
            pcm = e.get("pcm")
            if pcm:
                member = f"bank/{i}.pcm"
                blobs[member] = bytes(pcm)
                ent["member"] = member
            bank["entries"].append(ent)

    exe = getattr(m, "exe_path", None) or getattr(m, "_exe_path", None)
    exe_sha = None
    if exe and os.path.exists(exe):
        exe_sha = _sha(open(exe, "rb").read())

    man = {
        "version": SNAP_VERSION,
        "note": note,
        "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
        "exe": exe,
        "exe_sha256": exe_sha,
        "frames": getattr(m, "frames", 0),
        # Relative, so it still means something in another process at another
        # time. This is what keeps the guest's pacing continuous - see the
        # module docstring.
        "elapsed": m._elapsed(),
        "mode": m.mode,
        "regs": regs,
        "fp": fp,
        "vga": _grab(m, VGA_ATTRS),
        "input": _grab(m, INPUT_ATTRS),
        "dos": _grab(m, DOS_ATTRS),
        "files": files,
        "overlay": overlay,
        "xms": xms,
        "sb": sb,
        "bank": bank,
        "warnings": warnings,
    }
    return man, blobs


def save(m, path, note=""):
    """Capture and write. Returns the path written."""
    man, blobs = capture(m, note=note)
    return write(man, blobs, path)


def write(man, blobs, path):
    """Write an already-captured state. Returns the path written."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(man, indent=1))
        for name, data in blobs.items():
            z.writestr(name, data)
    os.replace(tmp, path)       # atomic, so a killed run leaves no half file
    for w in man["warnings"]:
        print(f"  [snap] WARNING: {w}")
    return path


def load(path):
    """Read a snapshot. Returns (manifest, {member: bytes})."""
    with zipfile.ZipFile(path) as z:
        man = json.loads(z.read("manifest.json"))
        blobs = {n.filename: z.read(n.filename) for n in z.infolist()
                 if n.filename != "manifest.json"}
    if man.get("version") != SNAP_VERSION:
        raise ValueError(f"snapshot version {man.get('version')} != "
                         f"{SNAP_VERSION}")
    return man, blobs


def describe(man):
    n = len(man.get("files", {}))
    xh = len(man.get("xms", {}).get("handles", {}))
    bits = [f"frame {man.get('frames')}", f"mode {man.get('mode'):#04x}",
            f"{xh} XMS handle(s)", f"{n} open file(s)"]
    if man.get("note"):
        bits.append(repr(man["note"]))
    return ", ".join(bits)


# ---------------------------------------------------------------- restore

def restore(m, man, blobs, force=False, verbose=True):
    """Put a captured state back into a freshly built machine.

    The machine must already have its natives installed - restoring over the top
    is what makes "capture with everything on, replay with one piece off" work.
    """
    exe = getattr(m, "exe_path", None) or getattr(m, "_exe_path", None)
    if man.get("exe_sha256") and exe and os.path.exists(exe):
        now = _sha(open(exe, "rb").read())
        if now != man["exe_sha256"]:
            msg = (f"snapshot was taken on a different image "
                   f"({man['exe_sha256'][:12]}… vs {now[:12]}…): every address "
                   f"in it may mean something else")
            if not force:
                raise ValueError(msg + " - pass force=True to override")
            print(f"  [snap] WARNING: {msg}")

    m.uc.mem_write(0, blobs["mem.bin"])
    for i in range(4):
        m.planes[i][:] = blobs[f"plane{i}.bin"]

    _put(m, VGA_ATTRS, man["vga"])
    _put(m, INPUT_ATTRS, man["input"])
    _put(m, DOS_ATTRS, man["dos"])

    if "chain4" not in man["vga"]:
        # Captured before chain4 was in the set. Rather than invalidate those
        # snapshots, infer it: anything in the planes means the game had
        # unchained, because in chained mode its writes go to the aperture.
        planar = any(any(blobs[f"plane{i}.bin"]) for i in range(4))
        m.chain4 = not planar
        print(f"  [snap] chain4 was not captured; inferred "
              f"{'Mode X, unchained' if planar else 'linear'} from the planes")

    # Segments before EIP is immaterial - none of these take effect until the
    # next instruction - but flags must be written like any other register, or
    # the restored machine runs with the direction and interrupt flags of a
    # freshly loaded one.
    for n, c in REGS.items():
        if n in man["regs"]:
            m.uc.reg_write(c, man["regs"][n])
    for n, v in (man.get("fp") or {}).items():
        c = FP_REGS.get(n)
        if c is not None:
            try:
                m.uc.reg_write(c, v)
            except Exception:
                pass

    m.overlay = {name: bytearray(blobs[member])
                 for name, member in man["overlay"].items()}

    m.handles = {}
    for hn, ent in man["files"].items():
        if ent.get("own"):
            data = blobs[ent["member"]]
        else:
            hp = host_path(ent["path"])
            if not os.path.exists(hp):
                print(f"  [snap] WARNING: {ent['path']!r} is gone from the "
                      f"game directory; handle {hn} restored empty")
                data = b""
            else:
                data = open(hp, "rb").read()
                if _sha(data) != ent["sha"]:
                    print(f"  [snap] WARNING: {ent['path']!r} on disk differs "
                          f"from the one this snapshot was taken against")
        h = Handle(ent["path"], data, ent["writable"])
        h.pos = ent["pos"]
        h.written = ent["written"]
        if ent.get("key"):
            setattr(h, "key", ent["key"])
        m.handles[int(hn)] = h

    _put(m.xms, XMS_ATTRS, man["xms"]["attrs"])
    m.xms.handles = {int(h): bytearray(blobs[e["member"]])
                     for h, e in man["xms"]["handles"].items()}

    if man.get("sb") and getattr(m, "sb", None) is not None:
        for k, v in man["sb"].items():
            setattr(m.sb, k, _coerce(getattr(m.sb, k, None), _dec(v)))

    if man.get("bank") and getattr(m, "bank", None) is not None:
        entries = []
        for ent in man["bank"]["entries"]:
            e = {k: _dec(v) for k, v in ent.items() if k != "member"}
            e["pcm"] = blobs[ent["member"]] if ent.get("member") else b""
            entries.append(e)
        m.bank.entries = entries
        m.bank.plays = _dec(man["bank"]["plays"])
        m.bank.by_extent = {(e.get("handle"), e.get("start"), e.get("length")): i
                            for i, e in enumerate(entries)}

    # Continue the clock from where the capture left off. Both the PIT counter and
    # the retrace bit come from _elapsed(); starting a restored machine near zero
    # reads to the game as time running backwards, and it skips frames to catch
    # up. Snapshots taken before this was carried simply do not have it, and
    # behave as they did.
    if man.get("elapsed") is not None:
        m.t0 = time.perf_counter() - man["elapsed"]
        # Re-base rather than charge the whole save-to-load gap to one interval.
        m.sb_last_tick = None

    _silence(m)

    m.frames = man.get("frames", 0)
    m.text_mode = _dec(man["vga"].get("text_mode", False))
    # Configuration, not state. Hooks and natives survive a restore because they
    # were never captured, but anything patched *into* guest memory is
    # overwritten by the memory coming back - and a capture taken before a patch
    # existed carries the original bytes. This is the seam where the layer that
    # installed such a thing puts it back; a machine without one is unaffected.
    again = getattr(m, "after_restore", None)
    if again is not None:
        again()
    if verbose:
        print(f"  [snap] restored {describe(man)}")
    return m


def _silence(m):
    """Stop playback and make the guest's voice table agree that nothing plays.

    Whatever was mid-sample at capture is not resumed - see the module
    docstring - but a snapshot taken while three voices were busy restores a
    table that says three voices are busy. The game asks that table, not the
    mixer: `is_sound_playing` walks the slots, and `play_sample` refuses when
    none is free. Leaving it as captured means those slots never free and the
    game quietly stops starting sounds.

    Only done when we own playback. With the emulated card the restored DSP and
    DMA state is consistent with the restored table, so there is nothing to fix.
    """
    v = getattr(m, "voices", None)
    if v is None:
        return
    for slot in list(getattr(v, "chan", {})):
        v.stop_voice(slot)
    if not getattr(v, "ok", False):
        return
    g = m.dgroup_base
    for slot in range(nsound.VOICES):
        m.write(g + nsound.VOICE_BASE + slot * nsound.VOICE_STRIDE,
                struct.pack("<6H", 0, 0, nsound.FREE_ID, 0, 0, 0))
        m.write(g + nsound.BUSY_BASE + slot * 2, struct.pack("<H", 0))
    m.write(g + nsound.ACTIVE_COUNT, struct.pack("<H", 0))


def restore_file(m, path, force=False, verbose=True):
    man, blobs = load(path)
    return restore(m, man, blobs, force=force, verbose=verbose)


def next_path(directory=SNAP_DIR, prefix="snap"):
    """A fresh numbered path, so repeated captures never overwrite."""
    os.makedirs(directory, exist_ok=True)
    n = 1
    while True:
        p = os.path.join(directory, f"{prefix}{n:03d}.snap")
        if not os.path.exists(p):
            return p
        n += 1


def compare(a_man, a_blobs, b_man, b_blobs):
    """Diff two snapshots. Returns a list of human-readable differences.

    This is the round-trip check: capture, restore into a new process, capture
    again, and the two must agree on every byte. Without it a restore that drops
    a plane or a register looks fine on screen and fails much later, in whatever
    test happens to depend on it.
    """
    out = []
    for name in sorted(set(a_blobs) | set(b_blobs)):
        x, y = a_blobs.get(name), b_blobs.get(name)
        if x is None or y is None:
            out.append(f"{name}: present in only one snapshot")
        elif x != y:
            n = sum(1 for i in range(min(len(x), len(y))) if x[i] != y[i])
            first = next((i for i in range(min(len(x), len(y)))
                          if x[i] != y[i]), None)
            out.append(f"{name}: {n} byte(s) differ, first at {first:#x}"
                       if first is not None else
                       f"{name}: lengths differ {len(x)} vs {len(y)}")
    for section in ("regs", "fp", "vga", "input", "dos", "files", "sb"):
        x, y = a_man.get(section) or {}, b_man.get(section) or {}
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k):
                out.append(f"{section}.{k}: {x.get(k)!r} != {y.get(k)!r}")
    return out
