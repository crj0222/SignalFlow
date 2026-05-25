import re
from typing import Optional

import discord

from analytics import AnalystStats, format_pct, format_trade_result
from models import Analyst, ParsedAlert


BRAND_COLOR = 0x2F80ED
SUCCESS_COLOR = 0x27AE60
LOSS_COLOR = 0xEB5757
WARNING_COLOR = 0xF2C94C
MUTED_COLOR = 0x95A5A6

ALERT_EMOJI = "\U0001f6a8"
BELL_EMOJI = "\U0001f514"
CHECK_EMOJI = "\u2705"
WARNING_EMOJI = "\u26a0\ufe0f"
FOOTER = "SignalFlow \u2022 Alert routing only, not financial advice"


def _value(value: object) -> str:
    if value in (None, ""):
        return "Not detected"
    text = str(value).strip()
    return "Not detected" if text.lower() in {"null", "none", "n/a", "na"} else text


def _instrument_label(parsed: ParsedAlert) -> str:
    if parsed.asset_type == "stock":
        return "Stock"
    if parsed.asset_type == "future":
        return "Futures"
    contract = parsed.contract
    if not contract:
        return "Trade"
    side = contract.strip().upper()[-1]
    if side == "C":
        return "Call Option"
    if side == "P":
        return "Put Option"
    return "Spread Option"


def _trade_line(parsed: ParsedAlert) -> str:
    parts = [part for part in [parsed.ticker, parsed.expiration if parsed.asset_type == "option" else None, parsed.contract] if part]
    trade = " ".join(parts) if parts else "Details not detected"
    if parsed.price is not None:
        trade = f"{trade} @{parsed.price:g}"
    return trade


def _price_line(parsed: ParsedAlert) -> str:
    if parsed.asset_type == "future":
        return f"{parsed.price:g}" if parsed.price is not None else "Not detected"
    return f"${parsed.price:.2f}" if parsed.price is not None else "Not detected"


def _gain_line(gain_pct: Optional[float]) -> str:
    if gain_pct is None:
        return "Not detected"
    sign = "+" if gain_pct >= 0 else ""
    return f"{sign}{gain_pct:.1f}%"


def _money(value: Optional[float]) -> str:
    return f"${value:.2f}" if value is not None else "Not detected"


def _color(brand_color: Optional[int], default: int = BRAND_COLOR) -> int:
    return brand_color if brand_color is not None else default


def _roll_cost_line(parsed: ParsedAlert) -> str:
    if parsed.roll_cost is None:
        return "Not detected"
    label = parsed.roll_cost_type.title() if parsed.roll_cost_type else "Cost"
    return f"{label} ${parsed.roll_cost:.2f}"


def _is_stop_like(parsed: ParsedAlert) -> bool:
    return bool(re.search(r"\b(STOPPED OUT|STOP HIT|STOP LOSS HIT|SL HIT|CUT(?:TING)?(?: HERE)?)\b", parsed.raw_text or "", re.IGNORECASE))


def _is_breakeven_like(parsed: ParsedAlert) -> bool:
    return bool(re.search(r"\b(B/E|AT BE|AT EVEN|BREAKEVEN|BREAK EVEN|SCRATCH)\b", parsed.raw_text or "", re.IGNORECASE))


def _set_footer(embed: discord.Embed, logo_url: Optional[str] = None) -> None:
    if logo_url:
        embed.set_footer(text=FOOTER, icon_url=logo_url)
    else:
        embed.set_footer(text=FOOTER)


def _add_server(embed: discord.Embed, guild_name: Optional[str]) -> None:
    if guild_name:
        embed.add_field(name="Server", value=guild_name, inline=False)


def start_embed() -> discord.Embed:
    embed = discord.Embed(
        title="SignalFlow",
        description=(
            "Premium alert routing for trading communities.\n\n"
            "SignalFlow sends analyst alerts only to users who opt in. It does not place trades, "
            "connect to brokerages, or provide financial advice."
        ),
        color=BRAND_COLOR,
    )
    embed.add_field(name="Get started", value="Use `/select_analysts` to choose whose alerts you want.", inline=False)
    embed.add_field(name="Control", value="Use `/pause_alerts` and `/resume_alerts` whenever you need quiet.", inline=False)
    _set_footer(embed)
    return embed


def help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="SignalFlow Help",
        description="Choose analyst alerts, track entries you actually take, and receive matching trim/close updates.",
        color=BRAND_COLOR,
    )
    embed.add_field(
        name="User commands",
        value="`/select_analysts`\n`/my_alerts`\n`/current_positions`\n`/analyst_stats`\n`/pause_alerts`\n`/resume_alerts`",
        inline=True,
    )
    embed.add_field(
        name="Alert buttons",
        value="`Took Trade` starts follow-up tracking.\n`Close Position` stops alerts for that trade.",
        inline=True,
    )
    embed.add_field(
        name="Admins",
        value="Use `/admin_dashboard` for the main server control panel.",
        inline=False,
    )
    _set_footer(embed)
    return embed


def entry_alert_embed(
    analyst: Analyst,
    parsed: ParsedAlert,
    guild_name: Optional[str] = None,
    logo_url: Optional[str] = None,
    brand_color: Optional[int] = None,
) -> discord.Embed:
    side = _instrument_label(parsed)
    style = _value(parsed.trade_note)
    description = f"**{_trade_line(parsed)}**"
    if style != "Not detected":
        description = f"{description}\n{style}"
    embed = discord.Embed(
        title=f"{ALERT_EMOJI} {side} Entry",
        description=description,
        color=_color(brand_color),
    )
    embed.add_field(name="Entry", value=_price_line(parsed), inline=True)
    if parsed.asset_type == "option":
        embed.add_field(name="Expiration", value=_value(parsed.expiration), inline=True)
    else:
        embed.add_field(name="Type", value=side, inline=True)
    embed.add_field(name="Analyst", value=analyst.name, inline=True)
    _add_server(embed, guild_name)
    _set_footer(embed, logo_url)
    return embed


def closed_entry_alert_embed(
    analyst: Analyst,
    parsed: ParsedAlert,
    close_action: Optional[str] = None,
    guild_name: Optional[str] = None,
    logo_url: Optional[str] = None,
    brand_color: Optional[int] = None,
) -> discord.Embed:
    normalized_action = (close_action or "").lower()
    if normalized_action == "roll_option":
        title = "Option Rolled"
        status = "Rolled by analyst"
        color = MUTED_COLOR
    elif normalized_action == "breakeven":
        title = "Break Even Exit"
        status = "Closed at breakeven"
        color = MUTED_COLOR
    elif normalized_action == "stop" or _is_stop_like(parsed):
        title = "Stopped Out"
        status = "Stopped out by analyst"
        color = LOSS_COLOR
    elif _is_breakeven_like(parsed):
        title = "Break Even Exit"
        status = "Closed at breakeven"
        color = MUTED_COLOR
    else:
        title = "Trade Closed"
        status = "Closed by analyst" if normalized_action in {"close", "exit"} else "No longer active"
        color = _color(brand_color, MUTED_COLOR)

    embed = discord.Embed(
        title=f"{BELL_EMOJI} {title}",
        description=f"**{_trade_line(parsed)}**\nThis entry alert is no longer active.",
        color=color,
    )
    embed.add_field(name="Entry", value=_price_line(parsed), inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Analyst", value=analyst.name, inline=True)
    _add_server(embed, guild_name)
    _set_footer(embed, logo_url)
    return embed


def exit_alert_embed(
    analyst: Analyst,
    parsed: ParsedAlert,
    possible: bool = False,
    gain_pct: Optional[float] = None,
    guild_name: Optional[str] = None,
    logo_url: Optional[str] = None,
    brand_color: Optional[int] = None,
) -> discord.Embed:
    if parsed.action == "trim":
        title = f"{WARNING_EMOJI} Possible Trim Alert" if possible else f"{BELL_EMOJI} Trim Alert"
        top_note = _value(parsed.trade_note)
    elif _is_stop_like(parsed):
        title = f"{WARNING_EMOJI} Possible Stop Alert" if possible else f"{BELL_EMOJI} Stopped Out"
        top_note = "Position closed"
    elif _is_breakeven_like(parsed):
        title = f"{WARNING_EMOJI} Possible Close Alert" if possible else f"{BELL_EMOJI} Break Even Exit"
        top_note = "Position closed"
    else:
        title = f"{WARNING_EMOJI} Possible Close Alert" if possible else f"{BELL_EMOJI} Trade Closed"
        top_note = "Position closed"

    color = WARNING_COLOR if possible else _color(brand_color)
    if parsed.action != "trim" and gain_pct is not None:
        color = SUCCESS_COLOR if gain_pct >= 0 else LOSS_COLOR
    if parsed.action != "trim" and _is_breakeven_like(parsed):
        color = MUTED_COLOR

    description = f"**{_trade_line(parsed)}**"
    if top_note != "Not detected":
        description = f"{description}\n{top_note}"

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )
    embed.add_field(name="Price", value=_price_line(parsed), inline=True)
    embed.add_field(name="Gain" if parsed.action == "trim" else "P/L", value=_gain_line(gain_pct), inline=True)
    if parsed.asset_type == "option":
        embed.add_field(name="Expiration", value=_value(parsed.expiration), inline=True)
    else:
        embed.add_field(name="Type", value=_instrument_label(parsed), inline=True)
    embed.add_field(name="Analyst", value=analyst.name, inline=True)
    _add_server(embed, guild_name)
    _set_footer(embed, logo_url)
    return embed


def position_update_embed(
    analyst: Analyst,
    parsed: ParsedAlert,
    reference_price: Optional[float] = None,
    guild_name: Optional[str] = None,
    logo_url: Optional[str] = None,
    brand_color: Optional[int] = None,
) -> discord.Embed:
    title_map = {
        "average_down": f"{BELL_EMOJI} Average Down",
        "average_up": f"{BELL_EMOJI} Average Up",
        "add": f"{BELL_EMOJI} Position Add",
    }
    embed = discord.Embed(
        title=title_map.get(parsed.action, f"{BELL_EMOJI} Position Update"),
        description=f"**{_trade_line(parsed)}**",
        color=_color(brand_color),
    )
    embed.add_field(name="Add Price", value=_price_line(parsed), inline=True)
    embed.add_field(name="Tracked Avg", value=_money(reference_price), inline=True)
    embed.add_field(name="Analyst", value=analyst.name, inline=True)
    _add_server(embed, guild_name)
    _set_footer(embed, logo_url)
    return embed


def roll_alert_embed(
    analyst: Analyst,
    parsed: ParsedAlert,
    old_ticker: Optional[str] = None,
    old_contract: Optional[str] = None,
    old_expiration: Optional[str] = None,
    old_price: Optional[float] = None,
    guild_name: Optional[str] = None,
    logo_url: Optional[str] = None,
    brand_color: Optional[int] = None,
) -> discord.Embed:
    old_parts = [part for part in [old_ticker or parsed.ticker, old_expiration, old_contract] if part]
    old_line = " ".join(old_parts) if old_parts else "Most recent tracked position"
    if old_price is not None:
        old_line = f"{old_line} @{old_price:g}"

    embed = discord.Embed(
        title=f"{BELL_EMOJI} Option Roll",
        description=f"**{_trade_line(parsed)}**",
        color=_color(brand_color),
    )
    embed.add_field(name="Old", value=old_line, inline=False)
    embed.add_field(name="New", value=_trade_line(parsed), inline=False)
    embed.add_field(name="Roll", value=_roll_cost_line(parsed), inline=True)
    embed.add_field(name="Analyst", value=analyst.name, inline=True)
    _add_server(embed, guild_name)
    _set_footer(embed, logo_url)
    return embed


def review_alert_embed(analyst: Analyst, parsed: ParsedAlert, guild_name: Optional[str] = None) -> discord.Embed:
    raw = parsed.raw_text.replace("\n", " ")
    if len(raw) > 900:
        raw = f"{raw[:897]}..."

    embed = discord.Embed(
        title=f"{WARNING_EMOJI} Alert Needs Review",
        description=raw,
        color=WARNING_COLOR,
    )
    embed.add_field(name="Action", value=parsed.action, inline=True)
    embed.add_field(name="Confidence", value=parsed.confidence, inline=True)
    embed.add_field(name="Analyst", value=analyst.name, inline=True)
    embed.add_field(name="Detected", value=_trade_line(parsed), inline=False)
    embed.add_field(
        name="Queue Actions",
        value="Use `Entry`, `Trim`, `Close`, or `Ignore` to save this wording as a server example. `Send Detected` routes it as currently classified.",
        inline=False,
    )
    _add_server(embed, guild_name)
    _set_footer(embed)
    return embed


def success_embed(message: str) -> discord.Embed:
    embed = discord.Embed(title=f"{CHECK_EMOJI} Done", description=message, color=SUCCESS_COLOR)
    _set_footer(embed)
    return embed


def warning_embed(message: str) -> discord.Embed:
    embed = discord.Embed(title=f"{WARNING_EMOJI} Review", description=message, color=WARNING_COLOR)
    _set_footer(embed)
    return embed


def list_embed(title: str, lines: list[str], empty: str) -> discord.Embed:
    embed = discord.Embed(title=title, description="\n".join(lines) if lines else empty, color=BRAND_COLOR)
    _set_footer(embed)
    return embed


def analyst_stats_embed(
    name: str,
    stats: AnalystStats,
    guild_name: Optional[str] = None,
    brand_color: Optional[int] = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=f"{name} Analyst Stats",
        description="Closed trades only. Trim-only updates are tracked but not counted as wins or losses.",
        color=_color(brand_color),
    )
    embed.add_field(
        name="Record",
        value=(
            f"**{stats.wins}W / {stats.losses}L**"
            f"{f' / {stats.breakevens}B/E' if stats.breakevens else ''}\n"
            f"Closed `{stats.closed_trades}`\n"
            f"Win Rate `{format_pct(stats.win_rate)}`\n"
            f"Open `{stats.open_trades}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="Averages",
        value=(
            f"Avg Return `{format_pct(stats.avg_return_pct)}`\n"
            f"Avg Win `{format_pct(stats.avg_win_pct)}`\n"
            f"Avg Loss `{format_pct(stats.avg_loss_pct)}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="Risk",
        value=(
            f"Stop-Out `{format_pct(stats.stop_out_rate)}`\n"
            f"Trim Rate `{format_pct(stats.trim_rate)}`"
        ),
        inline=True,
    )
    embed.add_field(
        name="Best / Worst",
        value=f"Best `{format_trade_result(stats.best_trade)}`\nWorst `{format_trade_result(stats.worst_trade)}`",
        inline=False,
    )
    if stats.asset_breakdown:
        lines = []
        for key, label in (("option", "Options"), ("stock", "Stocks"), ("future", "Futures"), ("unknown", "Other")):
            asset = stats.asset_breakdown.get(key)
            if asset:
                lines.append(f"{label}: `{asset.wins}W / {asset.losses}L` closed `{asset.closed}` avg `{format_pct(asset.avg_return_pct)}`")
        embed.add_field(name="By Type", value="\n".join(lines), inline=False)
    _add_server(embed, guild_name)
    _set_footer(embed)
    return embed
