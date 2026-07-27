#!/usr/bin/env python3
"""Record the last basic blocks, so a wild jump can name what jumped.

The iret theory was wrong: the guard on both irets in the image never fired, and
the stack words I read it from - 1d82 and 19a5 - are just SS and DS, the two values
this program pushes most often. Adjacent copies of them below SP mean nothing.

So stop inferring the transfer and record it. A block hook keeps the last 24 basic
blocks; on the wild jump, the previous one is the block that transferred, and its
last instruction is the culprit. Behind a flag, because it is a callback per basic
block and normal play should not pay for it.

    venv/bin/python edit_block_ring.py
"""
import sys

SRC = "native.py"

STATE_OLD = "        self.iret_reported = 0\n"
STATE_NEW = ("        self.iret_reported = 0\n"
             "        self.block_ring = deque(maxlen=24)\n")

INSTALL = '''    def install_block_trace(self):
        """Keep the last basic blocks executed, for the wild-jump report.

        One callback per basic block rather than per instruction, which is the
        cheapest granularity that still identifies a control transfer: the block
        before the wild one ends with whatever made the jump.
        """
        self.uc.hook_add(UC_HOOK_BLOCK, self._on_block)
        print("  [blocks] tracing basic blocks for the wild-jump report")

    def _on_block(self, uc, address, size, user):
        self.block_ring.append((address, size))

    def report_block_ring(self, img):
        """The blocks leading to here, and the tail of the one that transferred."""
        if not self.block_ring:
            print("  [blocks] no block trace; rerun with --trace-blocks")
            return
        md = _disasm16()
        norm = _fp_normalised(img) if md else None
        print("  [blocks] blocks leading here, oldest first:")
        for addr, size in list(self.block_ring):
            off = addr - self.image_base
            if 0 <= off < DGROUP_IMAGE_OFF:
                fn = find_function_start(img, off)
                place = f"in {fn:#07x}" if fn is not None else "in no function"
            elif 0 <= off < 0x20000:
                place = f"DGROUP+{off - DGROUP_IMAGE_OFF:#06x} (DATA)"
            else:
                place = "outside the image"
            print(f"  [blocks]   {addr:#07x} (+{size:#x}) {place}")

        # The transferring block is the last one that was still code.
        for addr, size in reversed(list(self.block_ring)[:-1]):
            off = addr - self.image_base
            if not (0 <= off < DGROUP_IMAGE_OFF) or md is None:
                continue
            print(f"  [blocks] tail of the block that transferred, "
                  f"{off:#07x}:")
            insns = list(md.disasm(norm[off:off + size + 16], off))
            for i in insns[-6:]:
                print(f"  [blocks]   {i.address:#07x}  {i.mnemonic} {i.op_str}")
            break

'''

WILD_OLD = '''        print("  [wild] state below is from the moment of arrival, so the words "
              "just under SP are whatever a bad return popped")
        self.crash_report()
'''
WILD_NEW = '''        print("  [wild] state below is from the moment of arrival, so the words "
              "just under SP are whatever a bad return popped")
        self.crash_report()
        self.report_block_ring(self.report_img)
'''

FLAG_OLD = '''    ap.add_argument("--bench-compose", action="store_true",'''
FLAG_NEW = '''    ap.add_argument("--trace-blocks", action="store_true",
                    help="record the last basic blocks, so a wild jump reports "
                         "the instruction that made it")
    ap.add_argument("--bench-compose", action="store_true",'''

MAIN_OLD = "    m.install_iret_guard(img)\n"
MAIN_NEW = ("    m.install_iret_guard(img)\n"
            "    if args.trace_blocks:\n"
            "        m.install_block_trace()\n")


def main():
    src = open(SRC).read()
    if "--bench-compose" not in src:
        # The bench flag was never applied; anchor on the plane-loop flag instead.
        globals()["FLAG_OLD"] = ('''    ap.add_argument("--native-plane-loop",'''
                                 ''' default=True,''')
        globals()["FLAG_NEW"] = (
            '''    ap.add_argument("--trace-blocks", action="store_true",\n'''
            '''                    help="record the last basic blocks, so a wild '''
            '''jump reports "\n'''
            '''                         "the instruction that made it")\n'''
            '''    ap.add_argument("--native-plane-loop", default=True,''')
    edits = [(STATE_OLD, STATE_NEW), (WILD_OLD, WILD_NEW),
             (FLAG_OLD, FLAG_NEW), (MAIN_OLD, MAIN_NEW),
             ("    def install_wild_jump_trap(self):",
              INSTALL + "    def install_wild_jump_trap(self):")]
    for old, new in edits:
        if src.count(old) != 1:
            print(f"anchor found {src.count(old)} times, nothing written: "
                  f"{old[:52]!r}")
            return 1
        src = src.replace(old, new, 1)
    open(SRC, "w").write(src)
    print("native.py updated: block ring added behind --trace-blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
