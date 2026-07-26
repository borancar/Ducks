#!/usr/bin/env python3
"""
Unpack a DIET-compressed 16-bit DOS executable by emulating its own unpacker.

Rather than reimplementing DIET's LZ77 stream format (where a subtly wrong
decode can look plausible), we load the EXE exactly as DOS would into a
Unicorn x86-16 real-mode CPU, let DIET's own loader stub decompress itself,
and stop at the handoff to the original entry point. Whatever is in memory
then IS the original image, by construction.

Nothing executes on the host: the guest code runs inside Unicorn.

Usage:
    python unpack_ducks.py ../Ducks.exe -o ../unpack/Ducks.unpacked.exe
    python unpack_ducks.py ../Ducks.exe --diagnose
"""
import argparse
import struct
import sys
from unicorn import *
from unicorn.x86_const import *

MEM_SIZE = 0x200000          # 2 MB, plenty for a ~117 KB DOS program
PSP_SEG = 0x0100
ENV_SEG = 0x00F0
MAX_INSNS = 200_000_000


class MZ:
    """Minimal MZ/EXE header."""

    def __init__(self, data):
        if data[:2] not in (b"MZ", b"ZM"):
            raise ValueError("not an MZ executable")
        (self.cblp, self.cp, self.crlc, self.cparhdr, self.minalloc,
         self.maxalloc, self.ss, self.sp, self.csum, self.ip, self.cs,
         self.lfarlc, self.ovno) = struct.unpack_from("<13H", data, 2)
        self.header_size = self.cparhdr * 16
        self.image_size = (self.cp - 1) * 512 + self.cblp - self.header_size
        if self.cblp == 0:
            self.image_size = self.cp * 512 - self.header_size
        self.data = data

    def relocs(self):
        out = []
        for i in range(self.crlc):
            off, seg = struct.unpack_from("<HH", self.data, self.lfarlc + i * 4)
            out.append((seg, off))
        return out

    def describe(self):
        return (f"  header      : {self.header_size} bytes "
                f"({self.cparhdr} paragraphs)\n"
                f"  image       : {self.image_size} bytes\n"
                f"  relocations : {self.crlc}\n"
                f"  entry CS:IP : {self.cs:04x}:{self.ip:04x}\n"
                f"  stack SS:SP : {self.ss:04x}:{self.sp:04x}\n"
                f"  minalloc    : {self.minalloc:#06x} paragraphs "
                f"({self.minalloc * 16} bytes)\n"
                f"  maxalloc    : {self.maxalloc:#06x}")


class Unpacker:
    def __init__(self, exe_bytes, load_seg, verbose=False):
        self.mz = MZ(exe_bytes)
        self.load_seg = load_seg
        self.load_base = load_seg * 16
        self.verbose = verbose

        self.writes = []          # (linear_addr, size, value) in load order
        self.write_lo = None
        self.write_hi = None
        self.site_hi = {}         # (cs,ip) -> highest linear address written
        self.site_lo = {}         # (cs,ip) -> lowest linear address written
        self.site_bytes = {}      # (cs,ip) -> total bytes written
        self.ints = []            # (int_no, ax) as encountered
        self.blocks_seen = 0
        self.handoff = None       # regs captured at the jump to original entry

        self.uc = Uc(UC_ARCH_X86, UC_MODE_16)
        self.uc.mem_map(0, MEM_SIZE)
        self._setup_dos()
        self._install_hooks()

    # ---------------------------------------------------------------- setup

    def _setup_dos(self):
        uc, mz = self.uc, self.mz

        # Environment block: a couple of plausible vars then the program path.
        env = b"COMSPEC=C:\\COMMAND.COM\x00PATH=C:\\\x00\x00\x01\x00C:\\DUCKS.EXE\x00"
        uc.mem_write(ENV_SEG * 16, env)

        # Program Segment Prefix. Only the fields a loader stub might read.
        psp = bytearray(0x100)
        psp[0x00:0x02] = b"\xcd\x20"                       # INT 20h
        top_of_mem = 0x9000                                # 576 KB conventional
        struct.pack_into("<H", psp, 0x02, top_of_mem)      # first para beyond alloc
        struct.pack_into("<H", psp, 0x2C, ENV_SEG)         # environment segment
        psp[0x50:0x53] = b"\xcd\x21\xcb"                   # INT 21h / RETF
        psp[0x80] = 0                                      # empty command tail
        psp[0x81] = 0x0D
        uc.mem_write(PSP_SEG * 16, bytes(psp))

        # The load image, verbatim after the header.
        image = mz.data[mz.header_size:mz.header_size + mz.image_size]
        uc.mem_write(self.load_base, image)
        self.image_loaded_len = len(image)

        # Apply the packed file's own relocations (DIET emits none, but be exact).
        for seg, off in mz.relocs():
            addr = self.load_base + seg * 16 + off
            val = struct.unpack("<H", uc.mem_read(addr, 2))[0]
            uc.mem_write(addr, struct.pack("<H", (val + self.load_seg) & 0xFFFF))

        # Register state exactly as DOS hands it over.
        uc.reg_write(UC_X86_REG_CS, (self.load_seg + mz.cs) & 0xFFFF)
        uc.reg_write(UC_X86_REG_IP, mz.ip)
        uc.reg_write(UC_X86_REG_SS, (self.load_seg + mz.ss) & 0xFFFF)
        uc.reg_write(UC_X86_REG_SP, mz.sp)
        uc.reg_write(UC_X86_REG_DS, PSP_SEG)
        uc.reg_write(UC_X86_REG_ES, PSP_SEG)
        uc.reg_write(UC_X86_REG_AX, 0x0000)   # both FCB drives valid
        uc.reg_write(UC_X86_REG_BX, 0)
        uc.reg_write(UC_X86_REG_CX, 0x00FF)
        uc.reg_write(UC_X86_REG_DX, PSP_SEG)
        uc.reg_write(UC_X86_REG_SI, mz.ip)
        uc.reg_write(UC_X86_REG_DI, mz.sp)
        uc.reg_write(UC_X86_REG_BP, 0x091C)

        self.entry_cs = (self.load_seg + mz.cs) & 0xFFFF
        self.entry_ip = mz.ip

    def _install_hooks(self):
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._on_write)
        self.uc.hook_add(UC_HOOK_INTR, self._on_intr)
        self.uc.hook_add(UC_HOOK_BLOCK, self._on_block)
        self.uc.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        self.uc.hook_add(UC_HOOK_INSN_INVALID, self._on_invalid)

    # ---------------------------------------------------------------- hooks

    def _on_write(self, uc, access, address, size, value, user):
        self.writes.append((address, size, value))
        if self.write_lo is None or address < self.write_lo:
            self.write_lo = address
        end = address + size
        if self.write_hi is None or end > self.write_hi:
            self.write_hi = end
        site = (uc.reg_read(UC_X86_REG_CS), uc.reg_read(UC_X86_REG_IP))
        if end > self.site_hi.get(site, 0):
            self.site_hi[site] = end
        if address < self.site_lo.get(site, 1 << 30):
            self.site_lo[site] = address
        self.site_bytes[site] = self.site_bytes.get(site, 0) + size

    def _on_intr(self, uc, intno, user):
        ax = uc.reg_read(UC_X86_REG_AX)
        ah = (ax >> 8) & 0xFF
        self.ints.append((intno, ax))
        if self.verbose:
            print(f"    INT {intno:02x}h AH={ah:02x} AX={ax:04x} "
                  f"at {uc.reg_read(UC_X86_REG_CS):04x}:"
                  f"{uc.reg_read(UC_X86_REG_IP):04x}")

        if intno == 0x21:
            if ah == 0x30:                      # get DOS version -> 5.0
                uc.reg_write(UC_X86_REG_AX, 0x0005)
                uc.reg_write(UC_X86_REG_BX, 0)
                uc.reg_write(UC_X86_REG_CX, 0)
                self._clear_cf()
                return
            if ah == 0x4A:                      # resize memory block -> ok
                self._clear_cf()
                return
            if ah in (0x48,):                   # allocate -> hand out high mem
                uc.reg_write(UC_X86_REG_AX, 0x8000)
                self._clear_cf()
                return
            if ah in (0x49, 0x25, 0x1A):        # free / set vector / set DTA
                self._clear_cf()
                return
            if ah == 0x35:                      # get interrupt vector
                uc.reg_write(UC_X86_REG_BX, 0)
                uc.reg_write(UC_X86_REG_ES, 0)
                self._clear_cf()
                return
            if ah in (0x4C, 0x00):              # terminate
                print(f"  ! program terminated via INT 21h AH={ah:02x}")
                uc.emu_stop()
                return
        if intno == 0x20:
            print("  ! program terminated via INT 20h")
            uc.emu_stop()
            return
        # Anything else: report it rather than silently faking success.
        print(f"  ! unhandled INT {intno:02x}h AH={ah:02x} - returning CF=0")
        self._clear_cf()

    def _on_block(self, uc, address, size, user):
        self.blocks_seen += 1
        cs = uc.reg_read(UC_X86_REG_CS)
        ip = address - cs * 16
        # DIET's final act is `jmp far <seg>:0000` into the original entry.
        # A basic block starting at offset 0 is that handoff.
        if ip == 0 and self.blocks_seen > 1:
            self.handoff = self._regs()
            uc.emu_stop()

    def _on_unmapped(self, uc, access, address, size, value, user):
        print(f"  ! unmapped access at {address:#x} size={size} "
              f"(CS:IP={uc.reg_read(UC_X86_REG_CS):04x}:"
              f"{uc.reg_read(UC_X86_REG_IP):04x})")
        return False

    def _on_invalid(self, uc, user):
        print(f"  ! invalid instruction at "
              f"{uc.reg_read(UC_X86_REG_CS):04x}:"
              f"{uc.reg_read(UC_X86_REG_IP):04x}")
        return False

    def _clear_cf(self):
        fl = self.uc.reg_read(UC_X86_REG_EFLAGS)
        self.uc.reg_write(UC_X86_REG_EFLAGS, fl & ~0x1)

    def _regs(self):
        r = UC_X86_REG_CS, UC_X86_REG_IP, UC_X86_REG_SS, UC_X86_REG_SP, \
            UC_X86_REG_DS, UC_X86_REG_ES, UC_X86_REG_AX, UC_X86_REG_BX, \
            UC_X86_REG_CX, UC_X86_REG_DX, UC_X86_REG_SI, UC_X86_REG_DI, \
            UC_X86_REG_BP
        names = "cs ip ss sp ds es ax bx cx dx si di bp".split()
        return dict(zip(names, (self.uc.reg_read(x) for x in r)))

    # ------------------------------------------------------------------ run

    def run(self):
        start = self.entry_cs * 16 + self.entry_ip
        try:
            self.uc.emu_start(start, 0, count=MAX_INSNS)
        except UcError as e:
            if self.handoff is None:
                raise RuntimeError(
                    f"emulation failed before handoff: {e} at "
                    f"{self.uc.reg_read(UC_X86_REG_CS):04x}:"
                    f"{self.uc.reg_read(UC_X86_REG_IP):04x}") from e
        if self.handoff is None:
            raise RuntimeError("never reached the handoff to original entry")
        return self.handoff

    def read_image(self, size):
        return bytes(self.uc.mem_read(self.load_base, size))

    def image_extent(self, min_span=0x1000):
        """Size of the genuine original image, in bytes.

        SS:SP cannot be used: it points into the stack/BSS, which a DOS EXE does
        not store in its file, and DIET deliberately parks its decompressor
        there. Nor can the overall write watermark be used: the stub's initial
        `rep movsw` copy-up leaves packed scratch above the real output.

        Discriminate by address span, not by write volume. The decompression
        output stores sweep a large contiguous range; the decompressor's stack
        pushes are equally high-volume but hammer only a few bytes, and would
        otherwise drag the estimate up into the scratch area.
        """
        out_sites = {}
        for s, hi in self.site_hi.items():
            if s[0] == self.load_seg:          # the stub's copy-up loop
                continue
            if hi - self.site_lo[s] < min_span:  # stack pushes and self-patches
                continue
            out_sites[s] = hi
        if not out_sites:
            raise RuntimeError("could not identify decompression output stores")
        self.output_sites = out_sites
        return max(out_sites.values()) - self.load_base


def build_exe(image, relocs, cs, ip, ss, sp, minalloc):
    """Serialize an image + relocation list into a standard MZ executable."""
    reloc_bytes = b"".join(struct.pack("<HH", off, seg) for seg, off in relocs)
    hdr_min = 0x1C + len(reloc_bytes)
    cparhdr = (hdr_min + 15) // 16
    header_size = cparhdr * 16
    total = header_size + len(image)
    cp = (total + 511) // 512
    cblp = total % 512

    hdr = bytearray(header_size)
    hdr[0:2] = b"MZ"
    struct.pack_into("<13H", hdr, 2,
                     cblp, cp, len(relocs), cparhdr, minalloc, 0xFFFF,
                     ss, sp, 0, ip, cs, 0x1C, 0)
    hdr[0x1C:0x1C + len(reloc_bytes)] = reloc_bytes
    return bytes(hdr) + image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("exe")
    ap.add_argument("-o", "--output")
    ap.add_argument("--diagnose", action="store_true",
                    help="report emulation results without writing an EXE")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    data = open(args.exe, "rb").read()
    print(f"=== packed input: {args.exe} ({len(data)} bytes) ===")
    print(MZ(data).describe())

    # Two runs at different load segments. Bytes that differ by exactly the
    # segment delta are the relocated words -- this identifies the relocation
    # set empirically, without trusting any reading of DIET's reloc encoding.
    seg_a, seg_b = 0x0110, 0x0310
    results = {}
    for seg in (seg_a, seg_b):
        print(f"\n=== emulating DIET stub at load segment {seg:#06x} ===")
        up = Unpacker(data, seg, verbose=args.verbose)
        regs = up.run()
        print(f"  handoff reached after {up.blocks_seen} basic blocks")
        print("  original entry CS:IP = "
              f"{regs['cs']:04x}:{regs['ip']:04x}  "
              f"(relative CS = {regs['cs'] - seg:#06x})")
        print(f"  original stack SS:SP = {regs['ss']:04x}:{regs['sp']:04x}  "
              f"(relative SS = {regs['ss'] - seg:#06x})")
        print(f"  writes: {len(up.writes)}, "
              f"linear range {up.write_lo:#x}..{up.write_hi:#x}")
        print(f"  INT calls: {len(up.ints)}")
        results[seg] = (up, regs)

    up_a, regs_a = results[seg_a]
    up_b, regs_b = results[seg_b]

    rel_cs = regs_a["cs"] - seg_a
    rel_ss = regs_a["ss"] - seg_a
    rel_sp = regs_a["sp"]
    assert regs_b["cs"] - seg_b == rel_cs, "entry CS not load-invariant"
    assert regs_b["ss"] - seg_b == rel_ss, "stack SS not load-invariant"

    image_size = up_a.image_extent()
    assert up_b.image_extent() == image_size, "image extent not load-invariant"

    print(f"\n=== recovered image ===")
    for s, hi in sorted(up_a.output_sites.items()):
        print(f"  output store {s[0]:04x}:{s[1]:04x} -> spans "
              f"{up_a.site_lo[s] - up_a.load_base:#08x}.."
              f"{hi - up_a.load_base:#08x}")
    print(f"  image size       : {image_size} bytes ({image_size:#x}), "
          f"{image_size / len(data):.2f}x the packed file")
    print(f"  SS:SP points to  : {rel_ss * 16 + rel_sp:#x} "
          f"(beyond the image, in BSS - as expected)")
    print(f"  copy-up scratch  : "
          f"{image_size:#x}..{up_a.write_hi - up_a.load_base:#x} (discarded)")

    img_a = up_a.read_image(image_size)
    img_b = up_b.read_image(image_size)

    # Identify relocations from the differential.
    delta = seg_b - seg_a
    relocs, mismatched = [], []
    i = 0
    while i < image_size - 1:
        wa = struct.unpack_from("<H", img_a, i)[0]
        wb = struct.unpack_from("<H", img_b, i)[0]
        if wa != wb:
            if (wa + delta) & 0xFFFF == wb:
                relocs.append(i)
                i += 2
                continue
            mismatched.append((i, wa, wb))
        i += 1

    print(f"  differential relocs: {len(relocs)}")
    if mismatched:
        print(f"  ! {len(mismatched)} differing bytes NOT explained by "
              f"relocation, first few: {mismatched[:5]}")

    if args.diagnose:
        return

    # Un-apply relocation so the emitted EXE is position independent again.
    img = bytearray(img_a)
    for off in relocs:
        val = struct.unpack_from("<H", img, off)[0]
        struct.pack_into("<H", img, off, (val - seg_a) & 0xFFFF)

    # DIET preserves the program's total memory footprint, so the original
    # minalloc is recoverable: packed(image + minalloc) - unpacked image.
    mz = MZ(data)
    footprint = mz.image_size + mz.minalloc * 16
    minalloc = (footprint - image_size + 15) // 16
    need_for_stack = (rel_ss * 16 + rel_sp - image_size + 15) // 16
    print(f"  minalloc         : {minalloc} paragraphs "
          f"(stack alone needs {need_for_stack}) ", end="")
    if minalloc >= need_for_stack:
        print("- covers the stack, consistent")
    else:
        print("- ! TOO SMALL to hold the stack")
        minalloc = need_for_stack

    reloc_pairs = [(off >> 4, off & 0xF) for off in relocs]
    out = build_exe(bytes(img), reloc_pairs, rel_cs, regs_a["ip"],
                    rel_ss, rel_sp, minalloc)

    path = args.output or args.exe + ".unpacked"
    with open(path, "wb") as f:
        f.write(out)
    print(f"\n=== wrote {path} ({len(out)} bytes) ===")


if __name__ == "__main__":
    sys.exit(main())
