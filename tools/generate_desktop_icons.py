from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = ROOT / "desktop" / "assets"
PAPER = "#ffffff"
ACCENT = "#6853d8"


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
        fill=ACCENT,
    )
    # Use the same open-book mark as the shared Web/desktop interface.
    stroke = max(1, round(17 * scale))
    def line(points: list[tuple[int, int]]) -> None:
        draw.line([(round(x * scale), round(y * scale)) for x, y in points],
                  fill=PAPER, width=stroke, joint="curve")

    line([(128, 140), (218, 140), (240, 146), (256, 158),
          (272, 146), (294, 140), (384, 140), (384, 362),
          (294, 362), (272, 368), (256, 380), (240, 368),
          (218, 362), (128, 362), (128, 140)])
    line([(256, 158), (256, 380)])
    for x in (174, 302):
        for y in (203, 252):
            draw.rounded_rectangle(box((x, y, x + 38, y + 14)),
                                   radius=round(7 * scale), fill=PAPER)
    return image


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    base = draw_icon(1024).resize((512, 512), Image.Resampling.LANCZOS)
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
