#!/usr/bin/env python3
"""Count plane-loop time in the native-time total.

native_time is summed in _on_native only, so the plane loops were missing from
the total in the report header while appearing in the ranked list under it. That
was a rounding error when the loops were 1s of a 60s run; now that the in-game
frame's whole drawing is inside one, the header read "2.19s of 77.7s" over a list
whose first line alone was 10.01s.

    venv/bin/python edit_fix_native_time.py
"""
import sys

SRC = "native.py"

OLD = '''        t0 = time.perf_counter()
        handler(self)
        self.native_secs[label] += time.perf_counter() - t0
'''

NEW = '''        t0 = time.perf_counter()
        handler(self)
        dt = time.perf_counter() - t0
        self.native_secs[label] += dt
        # Into the total as well, or the report header excludes the loops - and
        # they are now where nearly all the drawing happens.
        self.native_time += dt
'''


def main():
    src = open(SRC).read()
    if src.count(OLD) != 1:
        print(f"anchor found {src.count(OLD)} times, nothing written")
        return 1
    open(SRC, "w").write(src.replace(OLD, NEW, 1))
    print("native.py updated: plane-loop time counts toward the total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
