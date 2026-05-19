import discord
from typing import Optional

from models import Analyst, ParsedAlert


BRAND_COLOR = 0x2F80ED
SUCCESS_COLOR = 0x27AE60
LOSS_COLOR = 0xEB5757
WARNING_COLOR = 0xF2C94C
MUTED_COLOR = 0x95A5A6
FOOTER = "SignalFlow • Alert routing only, not financial advice"


def _value(value: object) -> str:
    if value in (None, ""):
        return "Not detected"
    text = str(value).strip()
    return "Not detected" if text.lower() in {"null", "none", "n/a", "na"} else text


def _option_side(contract: str | None) -> str:
    if not contract:
        return "Trade"
    side = contract.strip().upper()[-1]
    if side == "C":
        return "Call Option"
    if side == "P":
        return "Put Option"
    return "Spread Option"


def _trade_line(parsed: ParsedAlert) -> str:
    parts = [part for part in [parsed.ticker, parsed.expiration, parsed.contract] if part]
    trade = " ".join(parts) if parts else "Details not detected"
    if parsed.price is not None:
        trade = f"{trade} @{parsed.price:g}"
    return trade


def _price_line(parsed: ParsedAlert) -> str:
    return f"${parsed.price:.2f}" if parsed.price is not None else "Not detected"


def _gain_line(gain_pct: Optional[float]) -> str:
    if gain_pct is None:
        return "Not detected"
    sign = "+" if gain_pct >= 0 else ""
    return f"{sign}{gain_pct:.1f}%"


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
        description="Use SignalFlow to choose analyst alerts, track entries you actually take, and receive matching trim/close updates.",
        color=BRAND_COLOR,
    )
    embed.add_field(
        name="User commands",
        value="`/select_analysts`\n`/my_alerts`\n`/current_positions`\n`/pause_alerts`\n`/resume_alerts`",
        inline=True,
    )
    embed.add_field(
        name="Alert buttons",
        value="`Took Trade` starts follow-up tracking.\n`Close Position` stops alerts for that trade.",
        inline=True,
    )
    embed.add_field(
        name="Admins",
        value="Use `/admin_add_analyst`, `/admin_set_channel`, `/admin_list_analysts`, and `/admin_add_example` to manage routing.",
        inline=False,
    )
    _set_footer(embed)
    return embed


def entry_alert_embed(
    analyst: Analyst,
    parsed: ParsedAlert,
    guild_name: Optional[str] = None,
    logo_url: Optional[str] = None,
) -> discord.Embed:
    side = _option_side(parsed.contract)
    style = _value(parsed.trade_note)
    description = f"**{_trade_line(parsed)}**"
    if style != "Not detected":
        description = f"{description}\n{style}"
    embed = discord.Embed(
        title=f"🚨 {side} Entry",
        description=description,
        color=BRAND_COLOR,
    )
    embed.add_field(name="Entry", value=_price_line(parsed), inline=True)
    embed.add_field(name="Expiration", value=_value(parsed.expiration), inline=True)
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
) -> discord.Embed:
    if parsed.action == "trim":
        title = "⚠️ Possible Trim Alert" if possible else "🔔 Trim Alert"
        top_note = "Trim update"
    else:
        title = "⚠️ Possible Close Alert" if possible else "🔔 Trade Closed"
        top_note = "Position closed"
    color = WARNING_COLOR if possible else BRAND_COLOR
    if parsed.action != "trim" and gain_pct is not None:
        color = SUCCESS_COLOR if gain_pct >= 0 else LOSS_COLOR

    embed = discord.Embed(
        title=title,
        description=f"**{_trade_line(parsed)}**\n{top_note}",
        color=color,
    )
    embed.add_field(name="Price", value=_price_line(parsed), inline=True)
    if parsed.action == "trim":
        embed.add_field(name="Gain", value=_gain_line(gain_pct), inline=True)
    else:
        embed.add_field(name="P/L", value=_gain_line(gain_pct), inline=True)
    embed.add_field(name="Expiration", value=_value(parsed.expiration), inline=True)
    embed.add_field(name="Analyst", value=analyst.name, inline=True)
    _add_server(embed, guild_name)
    _set_footer(embed, logo_url)
    return embed


def success_embed(message: str) -> discord.Embed:
    embed = discord.Embed(title="✅ Done", description=message, color=SUCCESS_COLOR)
    _set_footer(embed)
    return embed


def warning_embed(message: str) -> discord.Embed:
    embed = discord.Embed(title="⚠️ Review", description=message, color=WARNING_COLOR)
    _set_footer(embed)
    return embed


def list_embed(title: str, lines: list[str], empty: str) -> discord.Embed:
    embed = discord.Embed(title=title, description="\n".join(lines) if lines else empty, color=BRAND_COLOR)
    _set_footer(embed)
    return embed
