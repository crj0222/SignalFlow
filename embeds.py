import discord

from models import Analyst, ParsedAlert


BRAND_COLOR = 0x2F80ED
SUCCESS_COLOR = 0x27AE60
WARNING_COLOR = 0xF2C94C
MUTED_COLOR = 0x95A5A6
FOOTER = "SignalFlow • Alert routing only, not financial advice"


def _value(value: object) -> str:
    return str(value) if value not in (None, "") else "Not detected"


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
    embed.set_footer(text=FOOTER)
    return embed


def entry_alert_embed(analyst: Analyst, parsed: ParsedAlert) -> discord.Embed:
    side = _option_side(parsed.contract)
    embed = discord.Embed(
        title=f"🚨 {side} Entry",
        description=f"**{_trade_line(parsed)}**",
        color=BRAND_COLOR,
    )
    embed.add_field(name="Analyst", value=analyst.name, inline=False)
    embed.add_field(name="Ticker", value=_value(parsed.ticker), inline=True)
    embed.add_field(name="Expiration", value=_value(parsed.expiration), inline=True)
    embed.add_field(name="Strike", value=_value(parsed.contract), inline=True)
    embed.add_field(name="Entry", value=f"${parsed.price:.2f}" if parsed.price is not None else "Not detected", inline=True)
    embed.add_field(name="Style", value=_value(parsed.trade_note), inline=True)
    embed.set_footer(text=FOOTER)
    return embed


def exit_alert_embed(analyst: Analyst, parsed: ParsedAlert, possible: bool = False) -> discord.Embed:
    if parsed.action == "stop":
        title = "⚠️ Possible Stop Alert" if possible else "⚠️ Stop-Out Alert"
    else:
        title = "⚠️ Possible Trim Alert" if possible else "🔔 Trim/Sell Alert"
    embed = discord.Embed(
        title=title,
        description=f"**{analyst.name}** posted an update for a trade you marked as taken.",
        color=WARNING_COLOR if possible else BRAND_COLOR,
    )
    embed.add_field(name="Ticker", value=_value(parsed.ticker), inline=True)
    embed.add_field(name="Contract", value=_value(parsed.contract), inline=True)
    embed.add_field(name="Expiration", value=_value(parsed.expiration), inline=True)
    embed.add_field(name="Price", value=f"${parsed.price:.2f}" if parsed.price is not None else "Not detected", inline=True)
    embed.add_field(name="Action", value=parsed.action.title(), inline=True)
    embed.set_footer(text=FOOTER)
    return embed


def success_embed(message: str) -> discord.Embed:
    embed = discord.Embed(title="✅ Done", description=message, color=SUCCESS_COLOR)
    embed.set_footer(text=FOOTER)
    return embed


def warning_embed(message: str) -> discord.Embed:
    embed = discord.Embed(title="⚠️ Review", description=message, color=WARNING_COLOR)
    embed.set_footer(text=FOOTER)
    return embed


def list_embed(title: str, lines: list[str], empty: str) -> discord.Embed:
    embed = discord.Embed(title=title, description="\n".join(lines) if lines else empty, color=BRAND_COLOR)
    embed.set_footer(text=FOOTER)
    return embed
