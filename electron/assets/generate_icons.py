"""Regenerate Electron platform icons from frontend/public/icon_light.png.

Outputs:
  electron/assets/icon.ico   — Windows (multi-resolution: 16, 32, 48, 64, 128, 256)
  electron/assets/icon.png   — Linux AppImage (512×512)
  electron/assets/icon.icns  — macOS dmg / dock (multi-resolution)

Run from the project root:
    python electron/assets/generate_icons.py

The source is the *light-theme* icon (the dark/visible robot). If you want the
opposite contrast — e.g. for a dark-only macOS dock or a custom light Windows
build — change SOURCE below to icon_dark.png.
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "frontend" / "public" / "icon_light.png"
OUT_DIR = ROOT / "electron" / "assets"

ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
PNG_SIZE = (512, 512)


def main() -> None:
    if not SOURCE.exists():
        raise SystemExit(f"Source icon not found: {SOURCE}")

    img = Image.open(SOURCE).convert("RGBA")
    print(f"Source: {SOURCE}  ({img.size[0]}×{img.size[1]} {img.mode})")

    ico_path = OUT_DIR / "icon.ico"
    img.save(ico_path, format="ICO", sizes=ICO_SIZES)
    print(f"  ->{ico_path.relative_to(ROOT)}  ({ico_path.stat().st_size:,} bytes)")

    png_path = OUT_DIR / "icon.png"
    img.resize(PNG_SIZE, Image.LANCZOS).save(png_path, format="PNG", optimize=True)
    print(f"  ->{png_path.relative_to(ROOT)}  ({png_path.stat().st_size:,} bytes)")

    icns_path = OUT_DIR / "icon.icns"
    img.save(icns_path, format="ICNS")
    print(f"  ->{icns_path.relative_to(ROOT)}  ({icns_path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
