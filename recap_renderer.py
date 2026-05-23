from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFont


WIDTH = 1240
BANNER_HEIGHT = 360
BACKGROUND = (39, 42, 47)
PANEL = (49, 53, 63)
LINE = (54, 58, 65)
TEXT = (235, 237, 243)
MUTED = (166, 168, 176)
BLUE = (174, 192, 232)
GREEN = (109, 241, 198)
RED = (255, 151, 161)
FOOTER = (96, 96, 106)


@dataclass(frozen=True)
class RecapTrade:
    analyst: str
    symbol: str
    entry: Optional[float | str] = None
    exit: Optional[float | str] = None
    result_pct: Optional[float] = None
    result_text: Optional[str] = None


@dataclass(frozen=True)
class RecapSection:
    title: str
    trades: list[RecapTrade] = field(default_factory=list)
    no_play_text: str = ""
    right_note: str = ""


@dataclass(frozen=True)
class RecapCard:
    brand: str
    subtitle: str
    wins: int
    losses: int
    sections: list[RecapSection]
    play_of_day: Optional[str] = None
    footer: str = "Evenstar Trading | Premium Recap"


def _font(size: int, bold: bool = False, serif: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if serif:
        candidates.extend(
            [
                r"C:\Windows\Fonts\georgiab.ttf" if bold else r"C:\Windows\Fonts\georgia.ttf",
                r"C:\Windows\Fonts\timesbd.ttf" if bold else r"C:\Windows\Fonts\times.ttf",
            ]
        )
    candidates.extend(
        [
            r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        ]
    )
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0], box[3] - box[1]


def _format_number(value: Optional[float | str]) -> str:
    if value is None:
        return "--"
    if isinstance(value, str):
        return value
    if abs(value - round(value)) < 0.001:
        return str(int(round(value)))
    return f"{value:g}"


def _result_text(trade: RecapTrade) -> str:
    if trade.result_text is not None:
        return trade.result_text
    if trade.result_pct is None:
        return "--"
    rounded = round(trade.result_pct)
    return f"{rounded}%"


def _result_color(trade: RecapTrade) -> tuple[int, int, int]:
    text = _result_text(trade).lower()
    if "x" in text or "sl" in text:
        return RED
    if trade.result_pct is not None and trade.result_pct < 0:
        return RED
    return GREEN


def _draw_space_banner(draw: ImageDraw.ImageDraw, rng: random.Random) -> None:
    for y in range(BANNER_HEIGHT):
        ratio = y / BANNER_HEIGHT
        color = (
            int(11 + 18 * ratio),
            int(16 + 20 * ratio),
            int(33 + 24 * ratio),
        )
        draw.line((0, y, WIDTH, y), fill=color)

    for _ in range(190):
        x = rng.randint(0, WIDTH)
        y = rng.randint(0, BANNER_HEIGHT - 12)
        radius = rng.choice([1, 1, 1, 2])
        glow = rng.randint(140, 255)
        draw.ellipse((x, y, x + radius, y + radius), fill=(glow, glow, min(255, glow + 20)))

    streaks = [
        ((130, -10), (25, 350), (249, 183, 151), 4),
        ((295, -5), (155, 360), (238, 209, 169), 3),
        ((845, -15), (690, 360), (83, 222, 231), 8),
        ((1090, -5), (955, 360), (244, 215, 177), 4),
        ((1210, 30), (1035, 350), (144, 187, 241), 3),
    ]
    for start, end, color, width in streaks:
        for offset in range(-2, 3):
            draw.line(
                (start[0] + offset, start[1], end[0] + offset, end[1]),
                fill=color,
                width=width,
            )

    for x, y, size in [(78, 132, 34), (1000, 178, 24), (205, 86, 18), (1115, 92, 14)]:
        draw.line((x - size, y, x + size, y), fill=(255, 255, 255), width=2)
        draw.line((x, y - size, x, y + size), fill=(255, 255, 255), width=2)

    title_font = _font(78, bold=True, serif=True)
    sub_font = _font(45, bold=True)
    title = "EVENSTAR"
    subtitle = "T R A D I N G"
    tw, _ = _text_size(draw, title, title_font)
    sw, _ = _text_size(draw, subtitle, sub_font)
    draw.text(((WIDTH - tw) / 2, 42), title, font=title_font, fill=(245, 245, 248))
    draw.text(((WIDTH - sw) / 2, 144), subtitle, font=sub_font, fill=(245, 245, 248))


def _draw_logo(draw: ImageDraw.ImageDraw, x: int, y: int, size: int) -> None:
    draw.ellipse((x, y, x + size, y + size), fill=(36, 42, 50), outline=(62, 68, 76), width=2)
    cx = x + size // 2
    cy = y + size // 2
    for angle in range(0, 360, 30):
        radians = math.radians(angle)
        length = size * (0.38 if angle % 60 == 0 else 0.25)
        draw.line(
            (cx, cy, cx + math.cos(radians) * length, cy + math.sin(radians) * length),
            fill=(197, 218, 237),
            width=2,
        )
    inner = size // 4
    draw.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), outline=(122, 145, 166), width=2)
    tiny = _font(10, bold=True)
    draw.text((x + 16, y + size - 26), "EVENSTAR", font=tiny, fill=(210, 215, 220))


def _draw_header(draw: ImageDraw.ImageDraw, card: RecapCard) -> int:
    y = BANNER_HEIGHT
    header_h = 155
    draw.rectangle((0, y, WIDTH, y + header_h), fill=BACKGROUND)
    draw.line((0, y, WIDTH, y), fill=(66, 70, 78), width=1)
    _draw_logo(draw, 48, y + 31, 92)

    title_font = _font(35, bold=True, serif=True)
    sub_font = _font(26)
    draw.text((168, y + 44), card.brand.upper(), font=title_font, fill=TEXT)
    draw.text((168, y + 91), card.subtitle, font=sub_font, fill=MUTED)

    score_font = _font(28, bold=True)
    score = f"{card.wins}W"
    loss = f"{card.losses}L"
    loss_w, _ = _text_size(draw, loss, score_font)
    draw.text((WIDTH - 166, y + 67), score, font=score_font, fill=GREEN)
    draw.text((WIDTH - 95, y + 67), "/", font=score_font, fill=MUTED)
    draw.text((WIDTH - 48 - loss_w, y + 67), loss, font=score_font, fill=RED)
    return y + header_h


def _estimate_height(card: RecapCard) -> int:
    height = BANNER_HEIGHT + 155 + 34
    for section in card.sections:
        height += 66
        height += max(len(section.trades), 1) * 65
        if section.no_play_text:
            height += 38
        height += 28
    if card.play_of_day:
        height += 132
    return height + 160


def _draw_section(draw: ImageDraw.ImageDraw, section: RecapSection, y: int) -> int:
    section_font = _font(24, bold=True)
    row_font = _font(26, bold=True)
    value_font = _font(25)
    result_font = _font(27, bold=True)

    draw.line((0, y, WIDTH, y), fill=LINE, width=1)
    y += 37
    draw.text((48, y), section.title.upper(), font=section_font, fill=BLUE)
    y += 59

    if not section.trades:
        no_play = section.no_play_text or "No plays"
        draw.text((48, y), no_play, font=value_font, fill=MUTED)
        if section.right_note:
            rw, _ = _text_size(draw, section.right_note, value_font)
            draw.text((WIDTH - 48 - rw, y), section.right_note, font=value_font, fill=MUTED)
        return y + 58

    for trade in section.trades:
        draw.text((48, y), trade.analyst, font=row_font, fill=TEXT)
        draw.text((218, y), trade.symbol, font=row_font, fill=BLUE)
        range_text = ""
        if trade.entry not in (None, "") or trade.exit not in (None, ""):
            range_text = f"{_format_number(trade.entry)} -> {_format_number(trade.exit)}"
        draw.text((558, y), range_text, font=value_font, fill=MUTED)
        result = _result_text(trade)
        rw, _ = _text_size(draw, result, result_font)
        draw.text((WIDTH - 48 - rw, y), result, font=result_font, fill=_result_color(trade))
        y += 51
        draw.line((48, y, WIDTH - 48, y), fill=LINE, width=1)
        y += 14

    if section.no_play_text:
        draw.text((48, y + 10), section.no_play_text, font=value_font, fill=(142, 144, 153))
        y += 51
    if section.right_note:
        rw, _ = _text_size(draw, section.right_note, value_font)
        draw.text((WIDTH - 48 - rw, y - 41), section.right_note, font=value_font, fill=MUTED)
    return y + 24


def _draw_play_of_day(draw: ImageDraw.ImageDraw, text: str, y: int) -> int:
    x = 48
    w = WIDTH - 96
    h = 100
    draw.rounded_rectangle((x, y, x + w, y + h), radius=7, fill=PANEL)
    draw.rectangle((x, y, x + 5, y + h), fill=BLUE)
    label_font = _font(21, bold=True)
    body_font = _font(26, bold=True)
    draw.text((x + 37, y + 28), "P L A Y  O F  T H E  D A Y", font=label_font, fill=BLUE)
    draw.text((x + 37, y + 63), text, font=body_font, fill=TEXT)
    return y + h + 36


def render_recap_card(card: RecapCard, output_path: str | Path) -> Path:
    output = Path(output_path)
    height = _estimate_height(card)
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    rng = random.Random(11)

    _draw_space_banner(draw, rng)
    y = _draw_header(draw, card)
    for section in card.sections:
        y = _draw_section(draw, section, y)

    if card.play_of_day:
        y = _draw_play_of_day(draw, card.play_of_day, y + 2)

    draw.line((0, height - 88, WIDTH, height - 88), fill=LINE, width=1)
    footer_font = _font(21)
    fw, _ = _text_size(draw, card.footer, footer_font)
    draw.text(((WIDTH - fw) / 2, height - 48), card.footer, font=footer_font, fill=FOOTER)

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, quality=95)
    return output


def sample_card() -> RecapCard:
    # Windows does not support %-d, so normalize the sample date manually.
    today = date.today().strftime("%B %d, %Y").replace(" 0", " ")
    return RecapCard(
        brand="Evenstar Trading",
        subtitle=f"Daily Recap | {today}",
        wins=17,
        losses=1,
        sections=[
            RecapSection(
                "Scott's Plays",
                [
                    RecapTrade("Alerts", "AVGO", 1.95, 3.7, 90),
                    RecapTrade("Alerts", "MSFT", "--", "--", result_text="X"),
                ],
            ),
            RecapSection(
                "Options",
                [
                    RecapTrade("Bishop", "ARM 235 C", 4.85, 22.5, 364),
                    RecapTrade("Bishop", "CRWD", 15.15, 91, 501),
                    RecapTrade("Cobain", "SPY", 1.75, 3.1, 77),
                    RecapTrade("Waxui", "SPX", 5.4, 11, 104),
                    RecapTrade("Shyamal", "SPX", 1.9, 6.5, 242),
                    RecapTrade("Hotshootah", "SPX", 2.1, 5.2, 148),
                ],
                no_play_text="Arc, Are, Expo: No plays",
            ),
            RecapSection("Futures", [RecapTrade("Expo", "1-0", "", "", result_text="")], right_note="Top Play: ES 24 pts"),
            RecapSection(
                "Stocks",
                [
                    RecapTrade("Mr. M", "ALAB", 270, 285, 6),
                    RecapTrade("Mr. M", "ARM", 230, 255, 11),
                ],
            ),
        ],
        play_of_day="Bishop | CRWD 501%",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a SignalFlow/Evenstar-style recap card.")
    parser.add_argument("--sample", action="store_true", help="Render the built-in sample recap.")
    parser.add_argument("--output", default="logs/recap_sample.png", help="Output image path.")
    args = parser.parse_args()
    card = sample_card()
    output = render_recap_card(card, args.output)
    print(output)


if __name__ == "__main__":
    main()
