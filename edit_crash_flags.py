#!/usr/bin/env python3
"""Put the flags in the crash report, since a trap flag is the story here.

The readme crash left 440 INT 01h sites stepping through BSS, which only happens
with TF set. Whether TF is still set at the fault, and what the rest of FLAGS looks
like, says whether the wild transfer came through an iret or a popf of garbage.

    venv/bin/python edit_crash_flags.py
"""
import sys

SRC = "native.py"

OLD = '''        print(f"  [crash] SS:SP {ss:04x}:{sp:04x} BP {bp:04x}")
'''

NEW = '''        fl = self._reg(UC_X86_REG_EFLAGS)
        named = [n for bit, n in ((0x100, "TF"), (0x200, "IF"), (0x400, "DF"),
                                  (0x001, "CF"), (0x040, "ZF"), (0x080, "SF"))
                 if fl & bit]
        print(f"  [crash] SS:SP {ss:04x}:{sp:04x} BP {bp:04x} "
              f"FLAGS {fl & 0xFFFF:04x} [{' '.join(named)}]")
        if fl & 0x100:
            print("  [crash] TF is set: the CPU was single-stepping, which is why "
                  "INT 01h appears in the interrupt report. Nothing in this port "
                  "sets it, so it arrived in a restored flags word - an iret or a "
                  "popf reading something that was not a flags word.")
'''


def main():
    src = open(SRC).read()
    if src.count(OLD) != 1:
        print("anchor missing, nothing written")
        return 1
    open(SRC, "w").write(src.replace(OLD, NEW, 1))
    print("native.py updated: flags in the crash report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
