"""Generate the app icons (apple-touch-icon 180, PWA 192/512, maskable 512) with Pillow: python -m bossmobile.icons"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).parent / "ui" / "icons"


def render(size: int, *, maskable: bool = False):
    from PIL import Image, ImageDraw, ImageFilter
    S = size * 4                                     # supersample, then downscale = smooth vector-like edges
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # background: deep night gradient #0a0c10 -> #1b2130 (full bleed; iOS rounds the corners itself)
    bg = Image.new("RGBA", (S, S))
    px = bg.load()
    for y in range(S):
        t = y / (S - 1)
        r, g, b = (int(10 + (27 - 10) * t), int(12 + (33 - 12) * t), int(16 + (48 - 16) * t))
        for x in range(S):
            px[x, y] = (r, g, b, 255)
    img.alpha_composite(bg)
    # warm glow behind the bolt
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    rad = S * 0.34
    gd.ellipse((S / 2 - rad, S / 2 - rad, S / 2 + rad, S / 2 + rad), fill=(217, 119, 87, 120))
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.09)))
    # lightning bolt (Bossman mark), scaled into the safe zone
    k = 0.62 if maskable else 0.74
    pts = [(13.6, 2), (4.5, 13.4), (9.7, 13.4), (9.0, 22), (18.5, 10.3), (13.1, 10.3), (13.6, 2)]
    off = S * (1 - k) / 2
    poly = [(off + x / 24 * S * k, off + y / 24 * S * k) for x, y in pts]
    d = ImageDraw.Draw(img)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).polygon([(x + S * 0.012, y + S * 0.02) for x, y in poly], fill=(0, 0, 0, 140))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(S * 0.015)))
    d.polygon(poly, fill=(245, 236, 222, 255))
    hi = [(off + x / 24 * S * k, off + y / 24 * S * k) for x, y in [(13.6, 2), (4.5, 13.4), (9.7, 13.4), (13.6, 2)]]
    d.polygon(hi, fill=(255, 250, 242, 255))
    return img.resize((size, size), Image.LANCZOS).convert("RGB")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size, name in ((180, "apple-touch-icon.png"), (192, "icon-192.png"), (512, "icon-512.png")):
        render(size).save(OUT / name, optimize=True)
    render(512, maskable=True).save(OUT / "icon-maskable-512.png", optimize=True)
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
