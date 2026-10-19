"""6-tile dark dashboard card generator for NAUTILUS (`CAGR`, `SHARPE`, `SORTINO`, `MAX DRAWDOWN`, `CALMAR`, `$1 BECOMES`).

Matches the dark-card aesthetic:
- Deep dark background (`#0b0c10`) with subtle rounded dark tiles (`#13151b` fill, `#232733` border)
- Grey uppercase tile headers (`CAGR`, `SHARPE`, `SORTINO`, `MAX DRAWDOWN`, `CALMAR`, `$1 BECOMES`)
- Large bold vibrant orange accent primary metric values (`#f97316` / `#ff7a18`)
- Subtle grey benchmark comparison line directly underneath (`SPY: 11.49%`, `SPY: 0.69`, etc.)
- Optional top header strip showing strategy vs benchmark and winning streak badge.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from PIL import Image, ImageDraw, ImageFont

from .metrics import PerformanceMetrics


# Palette matching the uploaded dark-card design
BG_COLOR = (10, 11, 15)
TILE_BG_COLOR = (19, 21, 27)
TILE_BORDER_COLOR = (36, 40, 52)
HEADER_TEXT_COLOR = (148, 155, 173)
ORANGE_ACCENT_COLOR = (249, 115, 22)
BENCHMARK_TEXT_COLOR = (130, 137, 155)
TITLE_TEXT_COLOR = (236, 240, 248)
BADGE_BG_COLOR = (34, 24, 18)
BADGE_BORDER_COLOR = (120, 62, 22)


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load clean sans-serif system font on Windows/Linux with graceful fallback."""
    from PIL import ImageFont

    candidates: Sequence[str] = (
        ("segoeuib.ttf", "arialbd.ttf", "calibrib.ttf", "DejaVuSans-Bold.ttf")
        if bold
        else ("segoeui.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf")
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _format_tiles(
    strategy: PerformanceMetrics,
    benchmark: PerformanceMetrics,
    benchmark_label: str,
) -> list[tuple[str, str, str]]:
    """Return the 6 (title, orange_value, grey_benchmark_line) tuples in 2x3 grid order."""
    b = benchmark_label.upper()
    return [
        (
            "CAGR",
            f"{strategy.cagr_pct:.2f}%",
            f"{b}: {benchmark.cagr_pct:.2f}%",
        ),
        (
            "SHARPE",
            f"{strategy.sharpe:.2f}",
            f"{b}: {benchmark.sharpe:.2f}",
        ),
        (
            "SORTINO",
            f"{strategy.sortino:.2f}",
            f"{b}: {benchmark.sortino:.2f}",
        ),
        (
            "MAX DRAWDOWN",
            f"{strategy.max_drawdown_pct:.2f}%",
            f"{b}: {benchmark.max_drawdown_pct:.2f}%",
        ),
        (
            "CALMAR",
            f"{strategy.calmar:.2f}",
            f"{b}: {benchmark.calmar:.2f}",
        ),
        (
            "$1 BECOMES",
            f"${strategy.dollar_becomes:.2f}",
            f"{b}: ${benchmark.dollar_becomes:.2f}",
        ),
    ]


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    initial_size: int,
    *,
    min_size: int = 8,
    bold: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font at initial_size and step down if rendered text exceeds max_width."""
    size = max(min_size, initial_size)
    while size > min_size:
        font = _load_font(size, bold=bold)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return font
        size = max(min_size, int(size * 0.88) if int(size * 0.88) < size else size - 1)
    return _load_font(min_size, bold=bold)


def render_nautilus_card(
    strategy: PerformanceMetrics,
    benchmark: PerformanceMetrics,
    *,
    strategy_label: str = "NAUTILUS",
    benchmark_label: str = "SPY",
    subtitle: str = "ДЕМО · READ-ONLY SHADOW ANALYTICS · BOSSMAN PULT",
    is_demo: bool = True,
    width: int = 1200,
    height: int = 680,
) -> bytes:
    """Render the 6-tile dark dashboard card as PNG bytes."""
    if width < 360 or height < 240:
        raise ValueError("Card dimensions must be at least 360x240")

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    pad_x = int(width * 0.04)
    pad_top = int(height * 0.14)
    pad_bottom = int(height * 0.06)
    gap_x = int(width * 0.022)
    gap_y = int(height * 0.04)

    header_avail_w = width - 2 * pad_x
    base_title = f"{strategy_label.upper()} vs {benchmark_label.upper()}"
    title_str = f"[ДЕМО] {base_title}" if is_demo and "ДЕМО" not in base_title else base_title
    eff_subtitle = (
        f"ДЕМО · {subtitle}"
        if is_demo and "ДЕМО" not in subtitle.upper() and "DEMO" not in subtitle.upper()
        else subtitle
    )

    # Fit subtitle badge to at most 52% of header width so it never collides with the title
    max_sub_text_w = max(80, int(header_avail_w * 0.52) - 28)
    font_header_sub = _fit_font(
        draw,
        eff_subtitle,
        max_sub_text_w,
        max(10, int(height * 0.021)),
        min_size=8,
        bold=False,
    )
    sub_bbox = draw.textbbox((0, 0), eff_subtitle, font=font_header_sub)
    sub_w = sub_bbox[2] - sub_bbox[0]
    sub_h = sub_bbox[3] - sub_bbox[1]
    badge_pad_x = max(8, int(width * 0.012))
    badge_pad_y = max(4, int(height * 0.010))
    badge_x1 = width - pad_x - sub_w - 2 * badge_pad_x
    badge_y1 = int(height * 0.040)
    badge_x2 = width - pad_x
    badge_y2 = badge_y1 + sub_h + 2 * badge_pad_y

    # Fit title to remaining left header width
    max_title_w = max(60, badge_x1 - pad_x - 12)
    font_header_title = _fit_font(
        draw,
        title_str,
        max_title_w,
        max(14, int(height * 0.038)),
        min_size=10,
        bold=True,
    )
    draw.text((pad_x, int(height * 0.042)), title_str, fill=TITLE_TEXT_COLOR, font=font_header_title)

    draw.rounded_rectangle(
        (badge_x1, badge_y1, badge_x2, badge_y2),
        radius=max(6, int(height * 0.015)),
        fill=BADGE_BG_COLOR,
        outline=BADGE_BORDER_COLOR,
        width=1,
    )
    draw.text(
        (badge_x1 + badge_pad_x, badge_y1 + badge_pad_y - sub_bbox[1]),
        eff_subtitle,
        fill=ORANGE_ACCENT_COLOR,
        font=font_header_sub,
    )

    # 3 columns x 2 rows grid
    cols = 3
    rows = 2
    avail_w = width - 2 * pad_x - (cols - 1) * gap_x
    avail_h = height - pad_top - pad_bottom - (rows - 1) * gap_y
    tile_w = avail_w // cols
    tile_h = avail_h // rows
    inner_tile_w = max(40, tile_w - max(16, int(tile_w * 0.10)))

    base_label_size = max(10, int(height * 0.026))
    base_value_size = max(18, int(height * 0.088))
    base_bench_size = max(10, int(height * 0.030))

    tiles = _format_tiles(strategy, benchmark, benchmark_label)

    for idx, (label, main_val, bench_val) in enumerate(tiles):
        r = idx // cols
        c = idx % cols
        x1 = pad_x + c * (tile_w + gap_x)
        y1 = pad_top + r * (tile_h + gap_y)
        x2 = x1 + tile_w
        y2 = y1 + tile_h

        draw.rounded_rectangle(
            (x1, y1, x2, y2),
            radius=max(8, int(min(tile_w, tile_h) * 0.06)),
            fill=TILE_BG_COLOR,
            outline=TILE_BORDER_COLOR,
            width=2,
        )

        cx = (x1 + x2) // 2

        # 1. Tile label (top center)
        font_tile_label = _fit_font(draw, label, inner_tile_w, base_label_size, min_size=8, bold=True)
        lbl_bbox = draw.textbbox((0, 0), label, font=font_tile_label)
        lbl_w = lbl_bbox[2] - lbl_bbox[0]
        lbl_y = y1 + int(tile_h * 0.16)
        draw.text((cx - lbl_w // 2, lbl_y), label, fill=HEADER_TEXT_COLOR, font=font_tile_label)

        # 2. Primary orange metric value (middle center)
        font_tile_value = _fit_font(draw, main_val, inner_tile_w, base_value_size, min_size=10, bold=True)
        val_bbox = draw.textbbox((0, 0), main_val, font=font_tile_value)
        val_w = val_bbox[2] - val_bbox[0]
        val_h = val_bbox[3] - val_bbox[1]
        val_y = y1 + int(tile_h * 0.48) - val_h // 2 - val_bbox[1]
        draw.text((cx - val_w // 2, val_y), main_val, fill=ORANGE_ACCENT_COLOR, font=font_tile_value)

        # 3. Benchmark comparison line (bottom center)
        font_tile_bench = _fit_font(draw, bench_val, inner_tile_w, base_bench_size, min_size=8, bold=False)
        b_bbox = draw.textbbox((0, 0), bench_val, font=font_tile_bench)
        b_w = b_bbox[2] - b_bbox[0]
        b_y = y1 + int(tile_h * 0.76)
        draw.text((cx - b_w // 2, b_y), bench_val, fill=BENCHMARK_TEXT_COLOR, font=font_tile_bench)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def save_nautilus_card(
    output_path: str | Path,
    strategy: PerformanceMetrics,
    benchmark: PerformanceMetrics,
    *,
    strategy_label: str = "NAUTILUS",
    benchmark_label: str = "SPY",
    subtitle: str = "ДЕМО · READ-ONLY SHADOW ANALYTICS · BOSSMAN PULT",
    is_demo: bool = True,
    width: int = 1200,
    height: int = 680,
) -> Path:
    """Render and write the 6-tile NAUTILUS PNG dashboard card to disk."""
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    png_bytes = render_nautilus_card(
        strategy,
        benchmark,
        strategy_label=strategy_label,
        benchmark_label=benchmark_label,
        subtitle=subtitle,
        is_demo=is_demo,
        width=width,
        height=height,
    )
    target.write_bytes(png_bytes)
    return target
