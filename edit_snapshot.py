#!/usr/bin/env python3
"""Add machine snapshots to native.py, and split main() so tests can reuse it.

Why: verifying a native needs the game to be in the state that calls it, and the
states that matter are only reachable by playing. The in-game frame loop runs
only during a level, the HUD loop runs twice a level and never again, and the
tally loop needs a level finished. Every verification run therefore cost a human
playing through, and a 900-second session that never left the text screen
recorded zero comparisons. Capturing the machine at a frame boundary turns that
into: play once, then start there as often as needed.

Three edits, and only the third is about snapshots:

1. `make_parser()` out of `main()`. replay.py then inherits every flag, so a
   snapshot captured with everything on can be replayed with one piece off -
   which is the whole point of the `--no-` forms.
2. `build_machine(args)` and `step_frame(...)` out of `main()`. There are already
   two copies of this loop (emulation.py and native.py) and their drift is a
   known problem; a third copy inside a test driver would be worse. Note the
   ordering that the split has to preserve: `install_native_fp` runs FINIT, so it
   must happen before a restore writes the register file back.
3. `take_snapshot()`, plus the three ways to trigger it - F2, `snapshot.request`
   for when the window does not have focus, and `--snapshot-at` for unattended
   capture - and `--load-snapshot` to restore one.

The capture point is the main loop's frame boundary in every case. That is the
only place where the x87 stack is empty (Borland's FP now runs on the real FPU,
whose register file Unicorn will not reliably hand back) and where no native
handler is part-way through reading its arguments off the live stack frame.

`m.exe_path` is recorded so a snapshot can refuse to restore onto a differently
unpacked image, where every address in it would mean something else.
"""

import sys

PATH = "native.py"

EDITS = [
    # ---------------------------------------------------------------- import
    ("""from nsound import NativeVoices, SoundBank
""",
     """from nsound import NativeVoices, SoundBank
import snapshot
"""),

    # ------------------------------------------------------- docstring note
    ("""Usage mirrors emulation.py otherwise (--scale, --blaster, F9/F10/F12).
\"\"\"""",
     """Usage mirrors emulation.py otherwise (--scale, --blaster, F9/F10/F12).

F2 - or `touch snapshot.request` - writes the whole machine to a file, and
--load-snapshot starts from one instead of from the program's entry point. That is
what makes the drawing states testable: reaching a level, the HUD or the tally
screen is a play-through, and a snapshot only has to be earned once. replay.py
runs them headlessly.
\"\"\""""),

    # ------------------------------------------------- parser out of main()
    ("""def main():
    ap = argparse.ArgumentParser(""",
     """def make_parser():
    ap = argparse.ArgumentParser("""),

    # ----------------------------- new flags, then build_machine out of main
    ("""    ap.add_argument("--unpacked", default="Ducks.unpacked.exe",
                    help="unpacked image, used to name functions when profiling")
    args = ap.parse_args()

    pygame.init()
    pygame.font.init()
    m = Native(args.exe, blaster=args.blaster, profile=args.profile,""",
     '''    ap.add_argument("--unpacked", default="Ducks.unpacked.exe",
                    help="unpacked image, used to name functions when profiling")
    ap.add_argument("--snapshot-dir", default=snapshot.SNAP_DIR,
                    help="where F2 and snapshot.request write their snapshots")
    ap.add_argument("--snapshot-at", default="",
                    help="comma-separated frame numbers to snapshot at, for "
                         "unattended capture, e.g. 400,900")
    ap.add_argument("--load-snapshot", default="",
                    help="restore this snapshot once the natives are installed, "
                         "instead of starting from the program's entry point")
    ap.add_argument("--force-snapshot", action="store_true",
                    help="restore even if the snapshot was taken on a different "
                         "image, where every address in it may mean something else")
    return ap


def build_machine(args):
    """Construct the machine and install everything the flags ask for.

    Split out of main() so replay.py builds an identical machine instead of a
    second copy of this sequence that drifts from it. Returns (machine, image).

    One ordering constraint worth keeping in view: install_native_fp() runs FINIT
    to set the control word a real FPU powers up with, so it has to happen before
    a snapshot restore writes the register file - and the natives have to be
    installed before a restored frame runs, or the guest draws it itself.
    """
    m = Native(args.exe, blaster=args.blaster, profile=args.profile,'''),

    # --------------------------------------------- record the image identity
    ("""               max_insns=1 << 62)
    m.voices = NativeVoices(m, bank=m.bank) if args.native_sound else None""",
     """               max_insns=1 << 62)
    # Recorded so a snapshot can refuse to restore onto a different image.
    m.exe_path = args.exe
    m.voices = NativeVoices(m, bank=m.bank) if args.native_sound else None"""),

    # ------------------- end of build_machine, then step_frame and new main()
    ('''    print(f"=== native-I/O port: {len(m.natives)} routine(s) serviced "
          f"natively, everything else emulated ===")
    audio = None''',
     '''    print(f"=== native-I/O port: {len(m.natives)} routine(s) serviced "
          f"natively, everything else emulated ===")
    return m, img


def step_frame(m, addr, args, img):
    """Run one display frame's worth of guest CPU. Returns (addr, running).

    Split out of main() so replay.py runs frames exactly the way a played session
    does. The slicing is not incidental: one sound service per chunk leaves the
    sound IRQ hundreds of thousands of instructions late, and the game then
    refills its DMA buffer too slowly to produce continuous audio.
    """
    slices = max(1, args.sound_slices if m.sb is not None else 1)
    step = max(1000, args.chunk // slices)
    for _ in range(slices):
        try:
            m.uc.emu_start(addr, 0, count=step)
        except UcError as e:
            print(f"  [cpu] {e} at {m._reg(UC_X86_REG_CS):04x}:"
                  f"{m._reg(UC_X86_REG_IP):04x}")
            m.crash_report(img)
            return addr, False
        if m.finished:
            print(f"  [dos] program exited: {m.finished}")
            return addr, False
        addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
        m.service_sound()
        # Read again after servicing: an injected sound IRQ pushes a frame and
        # moves CS:IP, so the address to resume from is not the one above.
        addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
    return addr, True


def take_snapshot(m, args, note):
    """Capture the machine. Only called at the main loop's frame boundary.

    That is the only point where this is safe: the x87 stack is empty there, and
    no native handler is part-way through reading its arguments off the live
    stack frame. snapshot.py checks the tag word and says so if it is not.
    """
    p = snapshot.save(m, snapshot.next_path(args.snapshot_dir), note=note)
    print(f"  [snap] wrote {p} ({os.path.getsize(p) / 1e6:.1f} MB, "
          f"frame {getattr(m, 'frames', 0)}, mode {m.mode:#04x}, {note})")
    return p


def main():
    ap = make_parser()
    args = ap.parse_args()
    pygame.init()
    pygame.font.init()
    m, img = build_machine(args)
    if args.load_snapshot:
        snapshot.restore_file(m, args.load_snapshot, force=args.force_snapshot)
    snap_at = {int(x, 0) for x in args.snapshot_at.split(",") if x.strip()}
    audio = None'''),

    # ------------------------------------------- the frame loop calls it now
    ("""    while running:
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
                m.crash_report(img)
                running = False
                break
            if m.finished:
                print(f"  [dos] program exited: {m.finished}")
                running = False
                break
            addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
            m.service_sound()
            addr = m._reg(UC_X86_REG_CS) * 16 + m._reg(UC_X86_REG_IP)
        if audio is not None:""",
     """    while running:
        addr, running = step_frame(m, addr, args, img)
        if audio is not None:"""),

    # ------------------------------------------------------------- F2 to snap
    ("""            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F5:""",
     """            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F2:
                    take_snapshot(m, args, f"F2 at frame {frames}")
                elif ev.key == pygame.K_F5:"""),

    # ------------------------------------- snapshot.request, without focus
    ("""        for name, action in (("trace.on", "on"), ("trace.off", "off"),
                             ("trace.report", "report"),
                             ("verify.on", "von"), ("verify.off", "voff"),
                             ("rate.report", "rate")):""",
     """        for name, action in (("trace.on", "on"), ("trace.off", "off"),
                             ("trace.report", "report"),
                             ("verify.on", "von"), ("verify.off", "voff"),
                             ("rate.report", "rate"),
                             ("snapshot.request", "snap")):"""),

    ("""                elif action == "rate":
                    m.report_rates()
                else:""",
     """                elif action == "rate":
                    m.report_rates()
                elif action == "snap":
                    take_snapshot(m, args,
                                  f"snapshot.request at frame {frames}")
                else:"""),

    # ------------------------------------------ unattended capture by frame
    ("""        frames += 1
        m.frames = frames
        clock.tick(60)""",
     """        frames += 1
        m.frames = frames
        if frames in snap_at:
            take_snapshot(m, args, f"--snapshot-at {frames}")
        clock.tick(60)"""),
]


def main():
    src = open(PATH).read()
    for old, _ in EDITS:
        n = src.count(old)
        if n != 1:
            print(f"anchor occurs {n} times, expected 1:\n{old}")
            return 1
    for old, new in EDITS:
        src = src.replace(old, new)
    open(PATH, "w").write(src)
    print(f"{PATH}: {len(EDITS)} edit(s) applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
