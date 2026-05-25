from __future__ import annotations

import argparse
import math
import random
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from parser import parse_gain_percent


WIDTH = 1320
BANNER_HEIGHT = 380
BACKGROUND = (39, 42, 47)
PANEL = (49, 53, 63)
LINE = (55, 59, 66)
TEXT = (235, 237, 243)
MUTED = (166, 168, 176)
BLUE = (174, 192, 232)
GREEN = (109, 241, 198)
RED = (255, 151, 161)
FOOTER = (96, 96, 106)
SIDE_PAD = 52
SYMBOL_X = 232
RANGE_X = 596


@dataclass(frozen=True)
class RecapTrade:
    analyst: str
    symbol: str
    entry: Optional[float | str] = None
    exit: Optional[float | str] = None
    result_pct: Optional[float] = None
    result_text: Optional[str] = None
    right_note: str = ""


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


@dataclass
class _TrackedTrade:
    analyst: str
    asset_type: str
    ticker: Optional[str]
    contract: Optional[str]
    entry_price: Optional[float]
    entry_id: int
    opened_at: str
    side: str = "long"
    exit_price: Optional[float] = None
    result_pct: Optional[float] = None
    result_text: Optional[str] = None
    closed: bool = False

    @property
    def symbol(self) -> str:
        if self.asset_type == "option" and self.contract:
            return " ".join(part for part in [self.ticker, self.contract] if part)
        return self.ticker or self.contract or "Unknown"


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


def _parse_db_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _display_date(value: date) -> str:
    return value.strftime("%B %d, %Y").replace(" 0", " ")


def _normalize_asset_type(value: Optional[str]) -> str:
    value = (value or "option").lower().strip()
    return value if value in {"option", "stock", "future"} else "option"


def _is_stop_or_loss(action: str, raw_text: str) -> bool:
    return bool(
        action in {"close", "stop", "exit"}
        and re.search(r"\b(?:STOP(?:PED)?(?: OUT| HIT)?|SL HIT|CUT(?:TING)?|LOSS|RED|INVALIDATED)\b", raw_text or "", re.IGNORECASE)
    )


def _entry_side(raw_text: str, asset_type: str) -> str:
    if asset_type in {"future", "stock"} and re.search(r"\b(?:SHORT|SELL SHORT|SOLD SHORT)\b", raw_text or "", re.IGNORECASE):
        return "short"
    return "long"


def _calc_pct(entry_price: Optional[float], exit_price: Optional[float], raw_text: str = "", side: str = "long") -> Optional[float]:
    if entry_price is not None and exit_price is not None and entry_price > 0:
        if side == "short":
            return ((entry_price - exit_price) / entry_price) * 100
        return ((exit_price - entry_price) / entry_price) * 100
    return parse_gain_percent(raw_text or "")


def _matches_trade(trade: _TrackedTrade, row: sqlite3.Row) -> bool:
    row_ticker = row["ticker"]
    row_contract = row["contract"]
    row_asset_type = _normalize_asset_type(row["asset_type"])
    if row_asset_type != "option" and trade.asset_type != row_asset_type:
        return False
    if row_ticker and trade.ticker and row_ticker != trade.ticker:
        return False
    if row_contract and trade.contract and row_contract != trade.contract:
        return False
    if row_ticker or row_contract:
        return bool((not row_ticker or trade.ticker == row_ticker) and (not row_contract or trade.contract == row_contract))
    return True


def _latest_matching_trade(open_trades: list[_TrackedTrade], row: sqlite3.Row) -> Optional[_TrackedTrade]:
    for trade in reversed(open_trades):
        if _matches_trade(trade, row):
            return trade
    return open_trades[-1] if open_trades else None


def _trade_result_from_update(trade: _TrackedTrade, row: sqlite3.Row) -> tuple[Optional[float], Optional[float], Optional[str]]:
    exit_price = row["price"]
    pct = _calc_pct(trade.entry_price, exit_price, row["raw_text"], trade.side)
    result_text = None
    if pct is None and _is_stop_or_loss(row["action"], row["raw_text"]):
        result_text = "X"
    return exit_price, pct, result_text


def _to_recap_trade(trade: _TrackedTrade) -> RecapTrade:
    return RecapTrade(
        analyst=trade.analyst,
        symbol=trade.symbol,
        entry=trade.entry_price,
        exit=trade.exit_price,
        result_pct=trade.result_pct,
        result_text=trade.result_text,
    )


def _build_futures_summary_rows(trades: Iterable[_TrackedTrade]) -> list[RecapTrade]:
    by_analyst: dict[str, dict[str, object]] = {}
    for trade in trades:
        bucket = by_analyst.setdefault(trade.analyst, {"wins": 0, "losses": 0, "best": None})
        is_loss = (trade.result_pct is not None and trade.result_pct < 0) or (trade.result_text or "").lower() == "x"
        is_win = trade.result_pct is not None and trade.result_pct >= 0
        if is_win:
            bucket["wins"] = int(bucket["wins"]) + 1
            best = bucket["best"]
            if not isinstance(best, _TrackedTrade) or (trade.result_pct or -10**9) > (best.result_pct or -10**9):
                bucket["best"] = trade
        elif is_loss:
            bucket["losses"] = int(bucket["losses"]) + 1

    rows: list[RecapTrade] = []
    for analyst, bucket in by_analyst.items():
        wins = int(bucket["wins"])
        losses = int(bucket["losses"])
        best = bucket["best"]
        top_play = best.symbol if isinstance(best, _TrackedTrade) else "-- --"
        rows.append(
            RecapTrade(
                analyst=analyst,
                symbol=f"{wins}-{losses}",
                entry="",
                exit="",
                result_text="",
                right_note=f"Top Play: {top_play}",
            )
        )
    return sorted(rows, key=lambda row: (-int(row.symbol.split("-", 1)[0]), row.analyst.lower()))


def build_recap_card_from_database(
    database_path: str | Path,
    guild_id: Optional[int] = None,
    recap_date: Optional[date] = None,
    brand: str = "SignalFlow",
    footer: str = "SignalFlow | Premium Recap",
) -> RecapCard:
    recap_date = recap_date or date.today()
    path = Path(database_path)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        if guild_id is None:
            guild = conn.execute("SELECT guild_id FROM guilds ORDER BY created_at DESC LIMIT 1").fetchone()
            if not guild:
                raise ValueError("No guilds found in the database yet.")
            guild_id = int(guild["guild_id"])

        rows = conn.execute(
            """
            SELECT al.*, a.name AS analyst_name
            FROM alert_logs al
            JOIN analysts a ON a.id = al.analyst_id
            WHERE al.guild_id = ?
            AND date(al.created_at) = ?
            ORDER BY al.created_at ASC, al.id ASC
            """,
            (guild_id, recap_date.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    open_by_analyst: dict[int, list[_TrackedTrade]] = {}
    completed: list[_TrackedTrade] = []

    for row in rows:
        action = row["action"]
        analyst_id = int(row["analyst_id"])
        asset_type = _normalize_asset_type(row["asset_type"])
        open_trades = open_by_analyst.setdefault(analyst_id, [])

        if action in {"entry", "roll_option"}:
            open_trades.append(
                _TrackedTrade(
                    analyst=row["analyst_name"],
                    asset_type=asset_type,
                    ticker=row["ticker"],
                    contract=row["contract"],
                    entry_price=row["price"],
                    entry_id=int(row["id"]),
                    opened_at=row["created_at"],
                    side=_entry_side(row["raw_text"], asset_type),
                )
            )
            continue

        if action in {"add", "average_down", "average_up"}:
            trade = _latest_matching_trade(open_trades, row)
            if trade and row["price"] is not None:
                if trade.entry_price is None:
                    trade.entry_price = row["price"]
                else:
                    trade.entry_price = (float(trade.entry_price) + float(row["price"])) / 2
            continue

        if action not in {"trim", "close", "exit", "stop"}:
            continue

        trade = _latest_matching_trade(open_trades, row)
        if not trade:
            continue

        exit_price, pct, result_text = _trade_result_from_update(trade, row)
        if pct is not None or result_text:
            current_pct = trade.result_pct if trade.result_pct is not None else -10**9
            if pct is None or pct >= current_pct:
                trade.exit_price = exit_price
                trade.result_pct = pct
                trade.result_text = result_text

        if action in {"close", "exit", "stop"}:
            trade.closed = True
            if trade not in completed:
                completed.append(trade)
            open_trades.remove(trade)

    seen = set()
    unique_completed = []
    for trade in completed:
        if trade.entry_id in seen:
            continue
        seen.add(trade.entry_id)
        unique_completed.append(trade)

    wins = sum(1 for trade in unique_completed if trade.result_pct is not None and trade.result_pct >= 0)
    losses = sum(
        1
        for trade in unique_completed
        if (trade.result_pct is not None and trade.result_pct < 0) or (trade.result_text or "").lower() == "x"
    )

    by_asset: dict[str, list[RecapTrade]] = {"option": [], "future": [], "stock": []}
    future_source: list[_TrackedTrade] = []
    for trade in unique_completed:
        if trade.asset_type == "future":
            future_source.append(trade)
            continue
        by_asset.setdefault(trade.asset_type, []).append(_to_recap_trade(trade))

    def sort_key(trade: RecapTrade) -> float:
        return trade.result_pct if trade.result_pct is not None else -10**8

    option_trades = sorted(by_asset["option"], key=sort_key, reverse=True)
    future_trades = _build_futures_summary_rows(future_source)
    stock_trades = sorted(by_asset["stock"], key=sort_key, reverse=True)
    sections = [
        RecapSection("Options", option_trades, no_play_text="No option plays" if not option_trades else ""),
        RecapSection("Futures", future_trades, no_play_text="No futures plays" if not future_trades else ""),
        RecapSection("Stocks", stock_trades, no_play_text="No stock plays" if not stock_trades else ""),
    ]
    best = max(
        (trade for trade in unique_completed if trade.result_pct is not None),
        key=lambda trade: trade.result_pct or -10**9,
        default=None,
    )
    play_of_day = None
    if best:
        play_of_day = f"{best.analyst} | {best.symbol} {round(best.result_pct or 0)}%"

    return RecapCard(
        brand=brand,
        subtitle=f"Daily Recap | {_display_date(recap_date)}",
        wins=wins,
        losses=losses,
        sections=sections,
        play_of_day=play_of_day,
        footer=footer,
    )


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


def _draw_right_note(
    draw: ImageDraw.ImageDraw,
    note: str,
    y: int,
    font: ImageFont.ImageFont,
) -> None:
    if ":" not in note:
        width, _ = _text_size(draw, note, font)
        draw.text((WIDTH - SIDE_PAD - width, y), note, font=font, fill=MUTED)
        return

    label, value = note.split(":", 1)
    label_text = f"{label}: "
    value_text = value.strip()
    label_w, _ = _text_size(draw, label_text, font)
    value_w, _ = _text_size(draw, value_text, font)
    x = WIDTH - SIDE_PAD - label_w - value_w
    draw.text((x, y), label_text, font=font, fill=MUTED)
    draw.text((x + label_w, y), value_text, font=font, fill=GREEN)


def _tracking(text: str) -> str:
    return " ".join(text.upper())


def _draw_space_banner(image: Image.Image, rng: random.Random) -> None:
    banner = Image.new("RGB", (WIDTH, BANNER_HEIGHT), (12, 16, 31))
    draw = ImageDraw.Draw(banner)
    for y in range(BANNER_HEIGHT):
        ratio = y / BANNER_HEIGHT
        color = (
            int(10 + 21 * ratio),
            int(15 + 22 * ratio),
            int(31 + 24 * ratio),
        )
        draw.line((0, y, WIDTH, y), fill=color)

    nebula = Image.new("RGBA", (WIDTH, BANNER_HEIGHT), (0, 0, 0, 0))
    ndraw = ImageDraw.Draw(nebula)
    for cx, cy, rx, ry, color in [
        (650, 125, 460, 125, (58, 194, 218, 68)),
        (940, 95, 380, 95, (52, 120, 212, 54)),
        (265, 210, 330, 110, (220, 154, 104, 44)),
        (1110, 260, 260, 90, (66, 158, 225, 42)),
    ]:
        ndraw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=color)
    banner = Image.alpha_composite(banner.convert("RGBA"), nebula.filter(ImageFilter.GaussianBlur(38))).convert("RGB")
    draw = ImageDraw.Draw(banner)

    for _ in range(250):
        x = rng.randint(0, WIDTH)
        y = rng.randint(0, BANNER_HEIGHT - 12)
        radius = rng.choice([1, 1, 1, 2])
        glow = rng.randint(145, 255)
        draw.ellipse((x, y, x + radius, y + radius), fill=(glow, glow, min(255, glow + 20)))

    streaks = [
        ((124, -12), (26, 365), (249, 183, 151), 5),
        ((300, -6), (154, 380), (238, 209, 169), 4),
        ((820, -18), (690, 380), (83, 222, 231), 9),
        ((1078, -8), (958, 380), (244, 215, 177), 5),
        ((1244, 25), (1050, 370), (144, 187, 241), 4),
        ((1195, -5), (1115, 210), (103, 185, 239), 2),
    ]
    for start, end, color, width in streaks:
        for offset in range(-3, 4):
            draw.line(
                (start[0] + offset, start[1], end[0] + offset, end[1]),
                fill=color,
                width=width,
            )

    for x, y, size in [(78, 134, 34), (1000, 178, 24), (205, 86, 18), (1115, 92, 14)]:
        draw.line((x - size, y, x + size, y), fill=(255, 255, 255), width=2)
        draw.line((x, y - size, x, y + size), fill=(255, 255, 255), width=2)

    title_font = _font(86, bold=True, serif=True)
    sub_font = _font(45, bold=True)
    title = "EVENSTAR"
    subtitle = "T R A D I N G"
    tw, _ = _text_size(draw, title, title_font)
    sw, _ = _text_size(draw, subtitle, sub_font)
    draw.text(((WIDTH - tw) / 2, 45), title, font=title_font, fill=(245, 245, 248))
    draw.text(((WIDTH - sw) / 2, 153), subtitle, font=sub_font, fill=(245, 245, 248))
    image.paste(banner, (0, 0))


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
    header_h = 165
    draw.rectangle((0, y, WIDTH, y + header_h), fill=BACKGROUND)
    draw.line((0, y, WIDTH, y), fill=(66, 70, 78), width=1)
    _draw_logo(draw, SIDE_PAD, y + 31, 92)

    title_font = _font(36, bold=True, serif=True)
    sub_font = _font(26)
    draw.text((180, y + 43), card.brand.upper(), font=title_font, fill=TEXT)
    draw.text((180, y + 92), card.subtitle, font=sub_font, fill=MUTED)

    score_font = _font(30, bold=True)
    score = f"{card.wins}W"
    loss = f"{card.losses}L"
    loss_w, _ = _text_size(draw, loss, score_font)
    draw.text((WIDTH - 164, y + 67), score, font=score_font, fill=GREEN)
    draw.text((WIDTH - 95, y + 67), "/", font=score_font, fill=MUTED)
    draw.text((WIDTH - SIDE_PAD - loss_w, y + 67), loss, font=score_font, fill=RED)
    return y + header_h


def _estimate_height(card: RecapCard) -> int:
    height = BANNER_HEIGHT + 165 + 34
    for section in card.sections:
        height += 74
        height += max(len(section.trades), 1) * 68
        if section.no_play_text:
            height += 42
        height += 28
    if card.play_of_day:
        height += 136
    return height + 160


def _draw_section(draw: ImageDraw.ImageDraw, section: RecapSection, y: int) -> int:
    section_font = _font(23, bold=True)
    row_font = _font(27, bold=True)
    value_font = _font(25)
    result_font = _font(28, bold=True)

    draw.line((0, y, WIDTH, y), fill=LINE, width=1)
    y += 40
    draw.text((SIDE_PAD, y), _tracking(section.title), font=section_font, fill=BLUE)
    y += 63

    if not section.trades:
        no_play = section.no_play_text or "No plays"
        draw.text((SIDE_PAD, y), no_play, font=value_font, fill=MUTED)
        if section.right_note:
            rw, _ = _text_size(draw, section.right_note, value_font)
            draw.text((WIDTH - SIDE_PAD - rw, y), section.right_note, font=value_font, fill=MUTED)
        return y + 62

    for trade in section.trades:
        draw.text((SIDE_PAD, y), trade.analyst, font=row_font, fill=TEXT)
        draw.text((SYMBOL_X, y), trade.symbol, font=row_font, fill=BLUE)
        range_text = ""
        if trade.entry not in (None, "") or trade.exit not in (None, ""):
            range_text = f"{_format_number(trade.entry)} → {_format_number(trade.exit)}"
        draw.text((RANGE_X, y), range_text, font=value_font, fill=MUTED)
        if trade.right_note:
            _draw_right_note(draw, trade.right_note, y, value_font)
        else:
            result = _result_text(trade)
            rw, _ = _text_size(draw, result, result_font)
            draw.text((WIDTH - SIDE_PAD - rw, y), result, font=result_font, fill=_result_color(trade))
        y += 53
        draw.line((SIDE_PAD, y, WIDTH - SIDE_PAD, y), fill=LINE, width=1)
        y += 15

    if section.no_play_text:
        draw.text((SIDE_PAD, y + 10), section.no_play_text, font=value_font, fill=(142, 144, 153))
        y += 55
    if section.right_note:
        rw, _ = _text_size(draw, section.right_note, value_font)
        draw.text((WIDTH - SIDE_PAD - rw, y - 41), section.right_note, font=value_font, fill=MUTED)
    return y + 24


def _draw_play_of_day(draw: ImageDraw.ImageDraw, text: str, y: int) -> int:
    x = SIDE_PAD
    w = WIDTH - SIDE_PAD * 2
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

    _draw_space_banner(image, rng)
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
            RecapSection("Futures", [RecapTrade("Expo", "0-1", "", "", result_text="", right_note="Top Play: -- --")]),
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
    parser.add_argument("--from-db", action="store_true", help="Render a recap from SignalFlow alert_logs.")
    parser.add_argument("--database", default="signalflow.sqlite3", help="SQLite database path.")
    parser.add_argument("--guild-id", type=int, help="Discord server ID. Defaults to the newest guild in the database.")
    parser.add_argument("--date", help="Recap date in YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--brand", default="SignalFlow", help="Brand/server name shown on the card.")
    parser.add_argument("--footer", default="SignalFlow | Premium Recap", help="Footer text shown on the card.")
    parser.add_argument("--output", default="logs/recap_sample.png", help="Output image path.")
    args = parser.parse_args()
    recap_date = _parse_db_date(args.date) if args.date else date.today()
    if args.from_db:
        card = build_recap_card_from_database(
            args.database,
            guild_id=args.guild_id,
            recap_date=recap_date,
            brand=args.brand,
            footer=args.footer,
        )
    else:
        card = sample_card()
    output = render_recap_card(card, args.output)
    print(output)


if __name__ == "__main__":
    main()
