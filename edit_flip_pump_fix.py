#!/usr/bin/env python3
"""Fix edit_flip_pump.py: the moved event block landed outside pump().

The previous script dedented the event block by four spaces before wrapping it in
`def pump():`. That was wrong twice over: the block was already at the eight-space
indent a nested function's body needs, so dedenting put it at the same level as the
`def` itself. The result was syntactically valid and semantically dead - `pump()`
contained nothing but its docstring and `nonlocal running`, and the event loop ran
once, inline, at definition time. No event was read after startup: no mouse, no
keys, no F12.

It survived review because the check that was run proved the wrong thing. `pump`
was confirmed to be *called* 163 times per 163 flips; nothing confirmed that
calling it did anything. That is verification-lessons.md exactly - the instrument
measured the call, not the effect.

So this script moves the block back inside the function, at the right indent, and
then **asserts the shape with ast** rather than trusting the string surgery: the
`pump` function defined inside `main` must contain a `for` loop over
`pygame.event.get()`. A structural post-condition would have caught the original
mistake, where a compile check could not.
"""

import ast
import sys

PATH = "native.py"

NONLOCAL = "        nonlocal running\n"
BLOCK_START = "    for ev in pygame.event.get():\n"
BLOCK_END = "                    m.release_pos[idx] = m.mouse_pos\n"


def pump_reads_events(src):
    """True if main()'s pump() actually iterates pygame.event.get()."""
    tree = ast.parse(src)
    for top in tree.body:
        if not (isinstance(top, ast.FunctionDef) and top.name == "main"):
            continue
        for node in top.body:
            if not (isinstance(node, ast.FunctionDef) and node.name == "pump"):
                continue
            for sub in ast.walk(node):
                if not isinstance(sub, ast.For):
                    continue
                call = sub.iter
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "get"
                        and isinstance(call.func.value, ast.Attribute)
                        and call.func.value.attr == "event"):
                    return True
    return False


def main():
    src = open(PATH).read()

    if pump_reads_events(src):
        print("pump() already reads events; nothing to do")
        return 1
    for a in (NONLOCAL, BLOCK_START, BLOCK_END):
        n = src.count(a)
        if n != 1:
            print(f"anchor occurs {n} times, expected 1:\n{a}")
            return 1

    i = src.index(BLOCK_START)
    j = src.index(BLOCK_END) + len(BLOCK_END)
    if not i < j:
        print("block end precedes block start")
        return 1
    block = src[i:j]

    indented = "".join(("    " + line) if line.strip() else line
                       for line in block.splitlines(keepends=True))

    # Cut it out, then put it back inside the function. Trailing blank lines left
    # behind by the cut go too, so the result reads normally.
    src = src[:i] + src[j:].lstrip("\n")
    src = src.replace(NONLOCAL, NONLOCAL + indented + "\n", 1)

    if not pump_reads_events(src):
        print("post-condition failed: pump() still does not read events; "
              "not writing")
        return 1

    open(PATH, "w").write(src)
    print(f"{PATH}: event block moved inside pump(); ast post-condition holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
