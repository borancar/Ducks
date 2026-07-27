#!/usr/bin/env python3
"""
Decode the text in Ducks' .egg data files.

The strings are obfuscated with a +1 character shift, so subtracting 1 from each
byte recovers plaintext ('UJN!UIF!GVSOJTI' -> 'TIM THE FURNISH'). This dumps the
readable text: episode and level names, credits, and hidden messages.

Also reports whether the file contains anything executable, or only data.
"""
import re
import sys
from collections import Counter

PATH = sys.argv[1] if len(sys.argv) > 1 else "game/Eggs/Main.egg"
data = open(PATH, "rb").read()
print(f"=== {PATH}: {len(data)} bytes ===")

dec = bytes((b - 1) & 0xFF for b in data)

# Runs of printable ASCII in the decoded stream, length >= 6.
pat = re.compile(rb"[\x20-\x7e]{6,}")
hits = [(m.start(), m.group()) for m in pat.finditer(dec)]
print(f"decoded printable runs (>=6 chars): {len(hits)}\n")


def interesting(s):
    """Filter out decoded runs that are really just uniform binary padding."""
    if len(set(s)) < 4:
        return False
    letters = sum(c.isalpha() or c == 32 for c in s.decode("latin1"))
    return letters / len(s) > 0.6


good = [(o, s) for o, s in hits if interesting(s)]
print(f"of which look like real text: {len(good)}\n")

print("=== decoded text ===")
for off, s in good:
    print(f"  {off:#09x}  {s.decode('latin1')}")

# Executable-content check: a DOS EXE/COM signature or an MZ header inside the
# data would be a red flag. Look for them in both raw and decoded forms.
print("\n=== executable-content check ===")
for label, blob in (("raw", data), ("decoded", dec)):
    for sig, what in ((b"MZ", "MZ executable header"),
                      (b"PE\x00\x00", "PE header"),
                      (b"This program", "DOS stub text"),
                      (b"diet", "DIET packer")):
        idx = blob.find(sig)
        # MZ occurs by chance in 2.4 MB of image data; only flag offset 0.
        if sig == b"MZ":
            hit = blob[:2] == b"MZ"
            print(f"  {label:8} {what:22}: "
                  f"{'PRESENT AT OFFSET 0' if hit else 'not at offset 0'}")
        else:
            print(f"  {label:8} {what:22}: "
                  f"{'found at ' + hex(idx) if idx >= 0 else 'absent'}")

print("\n=== byte-value distribution (is it compressed/image data?) ===")
c = Counter(data)
top = c.most_common(8)
print(f"  distinct byte values: {len(c)}/256")
print(f"  most common: {[(hex(b), n) for b, n in top]}")
