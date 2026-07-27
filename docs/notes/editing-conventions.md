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
hand or by an editor pass. Each change is a small Python script, `edit_*.py`,
committed alongside the change it made:

- read the whole file,
- assert each anchor string occurs **exactly once** — bail out printing the anchor
  if not,
- do the replacements,
- write the file back **once**, at the end.

A failed anchor therefore leaves the file untouched, and the script's docstring
records *why* the change was made, which is the part a diff cannot carry. The
scripts are one-shot by design: they are history, not a build step, and re-running
one after its edit has landed will fail its anchor check, which is the intended
behaviour.

## The repository root

The Python tooling is a git repository rooted at `unpack/`, not at the game
directory above it — that keeps the copyrighted game binaries and the 2.4 MB
`Eggs/Main.egg` out of history entirely, rather than relying on a `.gitignore`
being right.

Derived artefacts are untracked for the same reason: `Ducks.unpacked.exe` is
reproducible with `unpack_ducks.py`, so it is regenerated rather than shipped.

See [running-a-session](running-a-session.md).
