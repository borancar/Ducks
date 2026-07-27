#!/usr/bin/env python3
"""Make `native.py` with no arguments mean everything this port has built.

Every native started as opt-in, which was right while each one was a question. They
are all verified now, so the long launch line is just friction - and a line that
long invites a flag being dropped by accident, which is a silent change in what is
being tested.

Each becomes a BooleanOptionalAction defaulting to on, so the old spellings still
work exactly as before and every one can be turned off individually with --no-X.

    venv/bin/python edit_default_native.py
"""
import sys

SRC = "native.py"

# flag -> the help text it keeps
DEFAULT_ON = {
    "blaster": "emulate the Sound Blaster",
    "native-file": "serve the raw DOS read/lseek wrappers natively",
    "native-keyboard": "serve kbhit() natively, removing all key polling "
                       "through DOS",
    "native-mouse": "serve the game's mouse wrappers natively, removing all "
                    "INT 33h traffic",
    "sound-bank": "capture samples into an indexed bank as they load",
    "native-sound": "play voices through pygame instead of the emulated Sound "
                    "Blaster path",
    "native-xms": "service XMS with no interrupts: driver entry as a code hook, "
                  "plus the two INT 2Fh detection sites",
    "native-setup": "serve the C runtime's heap-resize, INT 10h wrapper and "
                    "one-shot startup interrupts natively",
    "native-plane-loop": "replace the guest's four four-plane drawing loops "
                         "natively",
    "native-fp": "put Borland's emulated x87 instructions back and let the real "
                 "FPU run them",
}

PARSER_OLD = '''    ap = argparse.ArgumentParser()
'''
PARSER_NEW = '''    ap = argparse.ArgumentParser(
        description="Run Ducks with the native port. Everything this port "
                    "replaces is on by default; each piece can be turned off "
                    "with its --no- form, which is how to check whether a "
                    "native is responsible for something.")
'''


def flag_block(name, help_text):
    """One BooleanOptionalAction argument, wrapped like the rest of the file."""
    lines, cur = [], f'    ap.add_argument("--{name}", default=True,'
    lines.append(cur)
    lines.append('                    action=argparse.BooleanOptionalAction,')
    words, cur = help_text.split(), '                    help="'
    for w in words:
        if len(cur) + len(w) + 1 > 78:
            lines.append(cur + '"')      # keep the space that ends the chunk
            cur = '                         "'
        cur += w + " "
    lines.append(cur.rstrip() + '")')
    return "\n".join(lines) + "\n"


def main():
    src = open(SRC).read()
    if src.count(PARSER_OLD) != 1:
        print("parser anchor missing, nothing written")
        return 1

    for name, help_text in DEFAULT_ON.items():
        # Match the existing declaration, however many help lines it has: from
        # `--name` up to the start of the next add_argument.
        needle = f'    ap.add_argument("--{name}", action="store_true"'
        if needle not in src:
            print(f"flag missing, nothing written: --{name}")
            return 1
        a = src.index(needle)
        b = src.index("    ap.add_argument(", a + 10)
        src = src[:a] + flag_block(name, help_text) + src[b:]

    src = src.replace(PARSER_OLD, PARSER_NEW, 1)
    open(SRC, "w").write(src)
    print(f"native.py updated: {len(DEFAULT_ON)} flags now default on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
