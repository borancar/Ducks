# Where changes go, and how they are applied

## Three layers, and new behaviour goes in the top one

```
trace_dos.py    DOS/BIOS shim, headless
  emulation.py  VGA + SDL, subclasses it
    native.py   subclasses that, replaces I/O and game routines with Python
```

**New behaviour goes in `native.py`.**

`trace_dos.py`'s value is precisely that it *cannot* alter the game directory: it
serves reads from the real files and satisfies writes from an in-memory overlay.
That is what makes it safe to point at an unfamiliar code path. Weakening it to
add a feature destroys the guarantee. `native.py` already overrides the hook
points (`_dos()`, the native tables), so behaviour belongs there — including save
persistence, which is why `native.py` writes and `trace_dos.py` does not.

`emulation.py` (Unicorn + SDL, reaches the menu with Mode X graphics correct and
working audio) is a reviewed working baseline. Avoid incidental edits to it;
edit it when the task genuinely requires it — sound work is the usual reason.

If a change to a lower layer looks unavoidable, say why rather than assuming, and
keep the opt-out that restores the safe mode (`--read-only`).

## `native.py` is edited by anchored scripts

`native.py` is large and heavily cross-referenced, so it is not edited in place by
hand or by an editor pass. Each change is applied by a small throwaway Python
script, `edit_*.py`, which:

- reads the whole file,
- asserts each anchor string occurs **exactly once** — bailing out and printing the
  anchor if not,
- does the replacements,
- writes the file back **once**, at the end.

A failed anchor therefore leaves the file untouched, which is the point: a
half-applied edit to this file is much worse than one that did not start.

**The scripts are not part of the repository.** `edit_*.py` is git-ignored: they are
how a change is made, not what the repository is, and each is genuinely one-shot —
re-running one after its edit has landed fails its anchor check by design. What
matters is the *reasoning*, and that belongs in the commit message, where someone
reading `git log` will find it, rather than in a docstring in a file nobody opens
again.

Where a script's structure is worth checking, assert it: string surgery that
produces valid but wrong Python is the failure mode here. One such edit moved a
block of event handling out of the function it was meant to land in — syntactically
fine, semantically dead — and an `ast` post-condition in the corrective script is
what made the second attempt provable rather than hopeful.

## The game lives under the repository, and never in it

Your copy of the game sits in `game/` at the repository root, and `.gitignore`
excludes it twice: by directory name, and again by file type (`*.exe`, `*.egg`,
`*.SG`, `*.dat`, `*.ico`, `*.URL`). Either rule alone would be enough, which is
the point — the payload being guarded is a 2.4 MB `Eggs/Main.egg` and a
copyrighted commercial executable, so one rule being wrong should not be enough
to publish them.

`GAME_DIR` in `trace_dos.py` is anchored on the file's own directory rather than
the working directory, so the tools run from anywhere; `DUCKS_GAME_DIR` overrides
it.

Derived artefacts are untracked for the same reason: `Ducks.unpacked.exe` is
reproducible with `unpack_ducks.py`, so it is regenerated rather than shipped.

See [running-a-session](running-a-session.md).
