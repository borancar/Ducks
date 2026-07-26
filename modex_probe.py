#!/usr/bin/env python3
"""
Try alternative Mode X interpretations against a capture, off-line.

Reads the four raw planes, the linear aperture and the palette dumped by
play.py's capture(), then renders the same memory under several candidate
layouts. Comparing them side by side identifies the correct one without having
to replay the game to the same screen.

Usage:
    python modex_probe.py debug/cap01
"""
import json
import os
import sys

import pygame

PLANE = 0x10000


def load(base):
    with open(f"{base}_planes.bin", "rb") as f:
        blob = f.read()
    planes = [blob[i * PLANE:(i + 1) * PLANE] for i in range(4)]
    with open(f"{base}_palette.bin", "rb") as f:
        praw = f.read()
    pal = [tuple(praw[i * 3:i * 3 + 3]) for i in range(256)]
    aperture = b""
    if os.path.exists(f"{base}_aperture.bin"):
        with open(f"{base}_aperture.bin", "rb") as f:
            aperture = f.read()
    state = {}
    if os.path.exists(f"{base}_state.json"):
        with open(f"{base}_state.json") as f:
            state = json.load(f)
    return planes, pal, aperture, state


def render_planar(planes, w, h, row_bytes, base_off):
    """Standard Mode X: pixel(x,y) = plane[x&3][base + y*row_bytes + (x>>2)]."""
    img = bytearray(w * h)
    span = w // 4
    for p in range(4):
        pl = planes[p]
        for y in range(h):
            src = base_off + y * row_bytes
            chunk = pl[src:src + span]
            if len(chunk) < span:
                chunk = chunk + bytes(span - len(chunk))
            img[y * w + p:y * w + w:4] = chunk
    return bytes(img)


def render_linear(buf, w, h, row_bytes, base_off):
    """Plain chained mode 13h, for comparison."""
    img = bytearray(w * h)
    for y in range(h):
        src = base_off + y * row_bytes
        chunk = buf[src:src + w]
        if len(chunk) < w:
            chunk = chunk + bytes(w - len(chunk))
        img[y * w:(y + 1) * w] = chunk
    return bytes(img)


def save(img, w, h, pal, path):
    surf = pygame.image.frombuffer(img, (w, h), "P")
    surf.set_palette(pal)
    pygame.image.save(surf.convert(24), path)


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "debug/cap01"
    planes, pal, aperture, state = load(base)
    print(f"=== {base} ===")
    if state:
        print(json.dumps(state, indent=2))

    nz = [sum(1 for b in pl if b) for pl in planes]
    print(f"\nnon-zero bytes per plane: {nz}")
    print(f"non-zero in linear aperture: {sum(1 for b in aperture if b)}")
    # Where does content stop in each plane? That reveals the real row stride.
    for i, pl in enumerate(planes):
        last = max((j for j, b in enumerate(pl) if b), default=-1)
        print(f"  plane {i}: last non-zero byte at {last:#07x} ({last})")

    pygame.init()
    outdir = base + "_probe"
    os.makedirs(outdir, exist_ok=True)

    cands = []
    for w, h in ((320, 200), (320, 240), (360, 200), (320, 400)):
        for row_bytes in (w // 4, 80, 84, 88, 96, 100, 128):
            cands.append(("planar", w, h, row_bytes, 0))
    for w, h in ((320, 200), (320, 240)):
        cands.append(("linear", w, h, w, 0))

    made = []
    for kind, w, h, rb, off in cands:
        need = off + (h - 1) * rb + w // 4
        if kind == "planar" and need > PLANE:
            continue
        name = f"{kind}_{w}x{h}_rb{rb}_off{off}.png"
        path = os.path.join(outdir, name)
        img = render_planar(planes, w, h, rb, off) if kind == "planar" \
            else render_linear(aperture, w, h, rb, off)
        save(img, w, h, pal, path)
        made.append(name)
    print(f"\nwrote {len(made)} candidate renderings to {outdir}/")
    for n in made:
        print(f"  {n}")


if __name__ == "__main__":
    sys.exit(main())
