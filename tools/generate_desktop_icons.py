from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = ROOT / "desktop" / "assets"
INK = "#17201e"
PAPER = "#ffffff"
TEAL = "#1f887d"
TEAL_DARK = "#176d65"


def draw_icon(size: int) -> Image.Image:
    scale = size / 512
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(value * scale) for value in values)

    margin = round(42 * scale)
    radius = round(96 * scale)
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=radius,
        fill=INK,
    )
    document = box((148, 104, 370, 408))
    draw.rounded_rectangle(document, radius=round(24 * scale), fill=PAPER)
    draw.polygon(
        [
            (round(300 * scale), round(104 * scale)),
            (round(370 * scale), round(174 * scale)),
            (round(300 * scale), round(174 * scale)),
        ],
        fill="#dce3e0",
    )
    draw.rounded_rectangle(box((185, 218, 334, 240)), radius=round(11 * scale), fill=TEAL)
    draw.rounded_rectangle(box((185, 264, 315, 286)), radius=round(11 * scale), fill=TEAL_DARK)
    draw.rounded_rectangle(box((185, 310, 282, 332)), radius=round(11 * scale), fill="#34618d")
    return image


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    base = draw_icon(512)
    base.save(ASSET_DIR / "papervault.png", optimize=True)
    base.save(
        ASSET_DIR / "papervault.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    try:
        base.save(ASSET_DIR / "papervault.icns", format="ICNS")
    except OSError:
        pass


if __name__ == "__main__":
    main()
