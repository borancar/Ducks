#!/usr/bin/env python3
"""Count plane-loop comparisons as attempted, so the summary is not nonsense.

verify_pending is bumped where a function-level native arms its return hook, and
the plane loops arm an exit hook instead - so a session that verified nothing but
loops printed "1415 of 0 attempted (0% actually compared)". The percentage is
there to catch comparisons that never complete, and it cannot do that while the
denominator ignores a whole class of them.

    venv/bin/python edit_fix_verify_pending.py
"""
import sys

SRC = "native.py"

OLD = '''        state["h"] = uc.hook_add(UC_HOOK_CODE, on_exit, None, exit_lin, exit_lin)
'''

NEW = '''        # Counted here as well as in _verify_native: this is a comparison that
        # has been armed and not yet completed, which is exactly what the
        # coverage percentage in the exit report is measuring.
        self.verify_pending += 1
        state["h"] = uc.hook_add(UC_HOOK_CODE, on_exit, None, exit_lin, exit_lin)
'''


def main():
    src = open(SRC).read()
    if src.count(OLD) != 1:
        print(f"anchor found {src.count(OLD)} times, nothing written")
        return 1
    open(SRC, "w").write(src.replace(OLD, NEW, 1))
    print("native.py updated: plane-loop comparisons count as attempted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
