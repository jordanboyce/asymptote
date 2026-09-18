#!/usr/bin/env python3
"""Derive the app's brand assets from the two source JPGs.

The sources in ``frontend/brand/`` are gradient artwork flattened onto a white
background. The app is theme-agnostic, so a white-backed JPG can't be dropped
into the UI directly: this script keys the white out to alpha and derives a
lightened variant for dark themes, where the
navy end of the gradient would otherwise sit at ~1.6:1 against ``base-100``.

Run it after replacing either source JPG:

    venv/Scripts/python.exe scripts/build_brand_assets.py
"""
from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
# Sources live outside public/ so the originals are not served or bundled.
SOURCE = FRONTEND / "brand"
PUBLIC = FRONTEND / "public"

# Alpha keying: distance from white, in the most-inked channel. Below FLOOR a
# pixel is JPEG ringing on the white field and is dropped; at or above SOLID it
# is ink. In between the edge is graded so antialiasing survives.
FLOOR = 14
SOLID = 70

# Dark-theme lift, in HSV value: keeps hue and saturation, raises brightness so
# the navy end of the gradient clears the dark base.
LIFT_BASE = 0.46
LIFT_SPAN = 0.54


def key_white(img: Image.Image) -> Image.Image:
    """White background -> alpha, with the ink colour un-premultiplied."""
    img = img.convert("RGB")
    out = Image.new("RGBA", img.size)
    src, dst = img.load(), out.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = src[x, y]
            d = 255 - min(r, g, b)
            if d <= FLOOR:
                continue
            a = 1.0 if d >= SOLID else (d - FLOOR) / (SOLID - FLOOR)
            # px = ink*a + white*(1-a)  ->  ink = (px - 255*(1-a)) / a
            inv = 255 * (1 - a)
            dst[x, y] = (
                min(255, max(0, round((r - inv) / a))),
                min(255, max(0, round((g - inv) / a))),
                min(255, max(0, round((b - inv) / a))),
                round(a * 255),
            )
    return out


def lift_for_dark(img: Image.Image) -> Image.Image:
    """Raise HSV value so the artwork reads on a dark surface."""
    out = img.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if not a:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            v = LIFT_BASE + LIFT_SPAN * v
            r, g, b = colorsys.hsv_to_rgb(h, s * 0.9, v)
            px[x, y] = (round(r * 255), round(g * 255), round(b * 255), a)
    return out


def squared(img: Image.Image, pad: float = 0.0) -> Image.Image:
    """Trim to the artwork, then centre it on a square transparent canvas."""
    img = img.crop(img.getbbox())
    side = round(max(img.size) * (1 + pad))
    canvas = Image.new("RGBA", (side, side))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


def flatten(img: Image.Image, bg: tuple[int, int, int]) -> Image.Image:
    """Composite onto an opaque background (iOS ignores icon transparency)."""
    plate = Image.new("RGBA", img.size, bg + (255,))
    return Image.alpha_composite(plate, img).convert("RGB")


def og_card(lockup: Image.Image, size=(1200, 630)) -> Image.Image:
    """Link-preview card: the lockup centred on an opaque plate.

    The lockup's tagline is raster text, so it only stays legible at this
    scale - it is deliberately not used as UI chrome.
    """
    art = lockup.copy()
    art.thumbnail((round(size[1] * 0.68), round(size[1] * 0.68)), Image.LANCZOS)
    card = Image.new("RGBA", size, (255, 255, 255, 255))
    card.paste(art, ((size[0] - art.width) // 2, (size[1] - art.height) // 2), art)
    return card.convert("RGB")


def write(img: Image.Image, name: str, size: int | None = None) -> None:
    if size:
        img = img.resize((size, size), Image.LANCZOS)
    img.save(PUBLIC / name, optimize=True)
    print(f"  {name}  {img.size[0]}x{img.size[1]}")


def main() -> None:
    mark = squared(key_white(Image.open(SOURCE / "Clio_logo_cropped.jpg")))
    lockup = key_white(Image.open(SOURCE / "Clio_hero_cropped.jpg"))
    lockup = lockup.crop(lockup.getbbox())

    print("brand assets ->", PUBLIC)
    # 256 is 4x the largest place the mark is drawn in the UI (64px); the
    # installable icon below is the only one that needs 512.
    write(mark, "clio-mark.png", 256)
    write(lift_for_dark(mark), "clio-mark-dark.png", 256)
    og_card(lockup).save(PUBLIC / "clio-og.png")
    print("  clio-og.png  1200x630")

    # PWA icons: "any" keeps the trimmed mark, "maskable" needs the safe-zone
    # padding so a platform's mask doesn't clip the artwork.
    write(squared(mark, pad=0.34), "clio-icon-maskable.png", 512)
    write(flatten(squared(mark, pad=0.18), (255, 255, 255)), "apple-touch-icon.png", 180)

    # Tab-icon sizes only - the PNG links in index.html cover high-DPI.
    ico = mark.resize((64, 64), Image.LANCZOS)
    ico.save(PUBLIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("  favicon.ico  multi-size")


if __name__ == "__main__":
    main()
