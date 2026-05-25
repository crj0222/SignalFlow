from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from parser import parse_gain_percent


@dataclass
class TradeRecord:
    analyst_id: int
    analyst_name: str
    asset_type: str
    ticker: Optional[str]
    contract: Optional[str]
    expiration: Optional[str]
    entry_price: Optional[float]
    exit_price: Optional[float]
    result_pct: Optional[float]
    result_text: Optional[str]
    close_reason: str
    trade_note: Optional[str]
    opened_at: str
    closed_at: str
    trim_count: int = 0
    max_update_pct: Optional[float] = None

    @property
    def symbol(self) -> str:
        parts = [self.ticker]
        if self.asset_type == "option":
            parts.extend([self.expiration, self.contract])
        return " ".join(part for part in parts if part) or "Unknown"


@dataclass
class OpenTrade:
    analyst_id: int
    analyst_name: str
    asset_type: str
    ticker: Optional[str]
    contract: Optional[str]
    expiration: Optional[str]
    entry_price: Optional[float]
    trade_note: Optional[str]
    raw_text: str
    opened_at: str
    entry_id: int
    trim_count: int = 0
    max_update_pct: Optional[float] = None

    @property
    def symbol(self) -> str:
        parts = [self.ticker]
        if self.asset_type == "option":
            parts.extend([self.expiration, self.contract])
        return " ".join(part for part in parts if part) or "Unknown"


@dataclass(frozen=True)
class AssetStats:
    closed: int = 0
    wins: int = 0
    losses: int = 0
    avg_return_pct: Optional[float] = None


@dataclass
class AnalystStats:
    analyst_id: int
    analyst_name: str
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0
    win_rate: Optional[float] = None
    avg_return_pct: Optional[float] = None
    avg_win_pct: Optional[float] = None
    avg_loss_pct: Optional[float] = None
    stop_out_rate: Optional[float] = None
    trim_rate: Optional[float] = None
    best_trade: Optional[TradeRecord] = None
    worst_trade: Optional[TradeRecord] = None
    open_trades: int = 0
    asset_breakdown: dict[str, AssetStats] = field(default_factory=dict)


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_asset_type(value: Optional[str]) -> str:
    value = (value or "option").lower().strip()
    return value if value in {"option", "stock", "future"} else "unknown"


def _display_analyst_name(name: str, discord_user_id: Optional[int] = None) -> str:
    clean = " ".join((name or "").split()).strip()
    clean = re.sub(r"^<@!?(\d+)>$", r"\1", clean)
    if clean.startswith("@"):
        clean = clean[1:].strip()
    if clean.isdigit():
        suffix = clean[-4:]
        return f"Analyst {suffix}"
    if discord_user_id and clean == str(discord_user_id):
        return f"Analyst {str(discord_user_id)[-4:]}"
    return clean or "Analyst"


def _is_short(raw_text: str, asset_type: str) -> bool:
    return asset_type in {"stock", "future"} and bool(re.search(r"\b(?:SHORT|SELL SHORT|SOLD SHORT)\b", raw_text or "", re.IGNORECASE))


def _is_breakeven(raw_text: str) -> bool:
    return bool(re.search(r"\b(?:B/E|AT B/E|AT BE|AT EVEN|BREAKEVEN|BREAK EVEN|SCRATCH)\b", raw_text or "", re.IGNORECASE))


def _close_reason(action: str, raw_text: str) -> str:
    if _is_breakeven(raw_text):
        return "breakeven"
    if action == "stop" or re.search(r"\b(?:STOP(?:PED)?(?: OUT| HIT)?|SL HIT|CUT(?:TING)?|INVALIDATED)\b", raw_text or "", re.IGNORECASE):
        return "stop"
    if action == "trim":
        return "trim"
    return "close"


def _result_pct(entry_price: Optional[float], exit_price: Optional[float], raw_text: str, is_short: bool = False) -> Optional[float]:
    if _is_breakeven(raw_text):
        return 0.0
    if entry_price is not None and exit_price is not None and entry_price > 0:
        change = entry_price - exit_price if is_short else exit_price - entry_price
        return (change / entry_price) * 100
    return parse_gain_percent(raw_text or "")


def _matches(trade: OpenTrade, row: sqlite3.Row) -> bool:
    row_ticker = row["ticker"]
    row_contract = row["contract"]
    row_asset_type = _normalize_asset_type(row["asset_type"])
    if row_asset_type != "unknown" and trade.asset_type != row_asset_type:
        return False
    if row_ticker and trade.ticker and row_ticker != trade.ticker:
        return False
    if row_contract and trade.contract and row_contract != trade.contract:
        return False
    if row_ticker or row_contract:
        return bool((not row_ticker or trade.ticker == row_ticker) and (not row_contract or trade.contract == row_contract))
    return True


def _latest_matching(open_trades: list[OpenTrade], row: sqlite3.Row) -> Optional[OpenTrade]:
    for trade in reversed(open_trades):
        if _matches(trade, row):
            return trade
    return open_trades[-1] if open_trades else None


def _avg(values: list[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _fetch_rows(
    database_path: str | Path,
    guild_id: Optional[int],
    analyst_id: Optional[int],
) -> tuple[int, list[sqlite3.Row]]:
    path = Path(database_path)
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")

    with _connect(path) as conn:
        if guild_id is None:
            guild = conn.execute("SELECT guild_id FROM guilds ORDER BY created_at DESC LIMIT 1").fetchone()
            if not guild:
                raise ValueError("No guilds found in the database yet.")
            guild_id = int(guild["guild_id"])

        params: list[object] = [guild_id]
        analyst_filter = ""
        if analyst_id is not None:
            analyst_filter = "AND al.analyst_id = ?"
            params.append(analyst_id)

        rows = conn.execute(
            f"""
            SELECT al.*, a.name AS analyst_name, a.discord_user_id AS analyst_discord_user_id
            FROM alert_logs al
            JOIN analysts a ON a.id = al.analyst_id
            WHERE al.guild_id = ?
            {analyst_filter}
            ORDER BY al.created_at ASC, al.id ASC
            """,
            params,
        ).fetchall()
    return guild_id, rows


def build_trade_records(
    database_path: str | Path,
    guild_id: Optional[int] = None,
    analyst_id: Optional[int] = None,
) -> tuple[list[TradeRecord], list[OpenTrade]]:
    _, rows = _fetch_rows(database_path, guild_id, analyst_id)
    open_by_analyst: dict[int, list[OpenTrade]] = {}
    closed: list[TradeRecord] = []

    for row in rows:
        action = row["action"]
        analyst = int(row["analyst_id"])
        asset_type = _normalize_asset_type(row["asset_type"])
        open_trades = open_by_analyst.setdefault(analyst, [])

        if action in {"entry", "roll_option"}:
            open_trades.append(
                OpenTrade(
                    analyst_id=analyst,
                    analyst_name=_display_analyst_name(row["analyst_name"], row["analyst_discord_user_id"]),
                    asset_type=asset_type,
                    ticker=row["ticker"],
                    contract=row["contract"],
                    expiration=row["expiration"],
                    entry_price=row["price"],
                    trade_note=row["trade_note"],
                    raw_text=row["raw_text"] or "",
                    opened_at=row["created_at"],
                    entry_id=int(row["id"]),
                )
            )
            continue

        if action in {"add", "average_down", "average_up"}:
            trade = _latest_matching(open_trades, row)
            if trade and row["price"] is not None:
                if trade.entry_price is None:
                    trade.entry_price = row["price"]
                else:
                    trade.entry_price = (float(trade.entry_price) + float(row["price"])) / 2
            continue

        if action not in {"trim", "close", "exit", "stop"}:
            continue

        trade = _latest_matching(open_trades, row)
        if not trade:
            continue

        update_pct = _result_pct(trade.entry_price, row["price"], row["raw_text"] or "", _is_short(trade.raw_text, trade.asset_type))
        if update_pct is not None:
            trade.max_update_pct = update_pct if trade.max_update_pct is None else max(trade.max_update_pct, update_pct)
        if action == "trim":
            trade.trim_count += 1
            continue

        result_pct = update_pct
        reason = _close_reason(action, row["raw_text"] or "")
        result_text = None
        if result_pct is None and reason == "stop":
            result_text = "X"

        closed.append(
            TradeRecord(
                analyst_id=trade.analyst_id,
                analyst_name=trade.analyst_name,
                asset_type=trade.asset_type,
                ticker=trade.ticker,
                contract=trade.contract,
                expiration=trade.expiration,
                entry_price=trade.entry_price,
                exit_price=row["price"],
                result_pct=result_pct,
                result_text=result_text,
                close_reason=reason,
                trade_note=trade.trade_note,
                opened_at=trade.opened_at,
                closed_at=row["created_at"],
                trim_count=trade.trim_count,
                max_update_pct=trade.max_update_pct,
            )
        )
        open_trades.remove(trade)

    open_trades = [trade for trades in open_by_analyst.values() for trade in trades]
    return closed, open_trades


def build_analyst_stats(
    database_path: str | Path,
    guild_id: Optional[int] = None,
    analyst_id: Optional[int] = None,
) -> list[AnalystStats]:
    closed, open_trades = build_trade_records(database_path, guild_id, analyst_id)
    analyst_ids = {trade.analyst_id for trade in closed} | {trade.analyst_id for trade in open_trades}
    stats_by_id: dict[int, AnalystStats] = {}

    for aid in analyst_ids:
        name = next((trade.analyst_name for trade in closed if trade.analyst_id == aid), None)
        if name is None:
            name = next((trade.analyst_name for trade in open_trades if trade.analyst_id == aid), "Analyst")
        stats_by_id[aid] = AnalystStats(analyst_id=aid, analyst_name=name)

    for aid, stats in stats_by_id.items():
        trades = [trade for trade in closed if trade.analyst_id == aid]
        open_count = sum(1 for trade in open_trades if trade.analyst_id == aid)
        pct_trades = [trade for trade in trades if trade.result_pct is not None]
        wins = [trade for trade in pct_trades if (trade.result_pct or 0) > 0]
        breakevens = [trade for trade in pct_trades if abs(trade.result_pct or 0) < 0.001]
        losses = [
            trade
            for trade in trades
            if (trade.result_pct is not None and trade.result_pct < 0) or (trade.result_text or "").lower() == "x"
        ]

        stats.closed_trades = len(trades)
        stats.wins = len(wins)
        stats.losses = len(losses)
        stats.breakevens = len(breakevens)
        stats.open_trades = open_count
        stats.win_rate = (len(wins) / (len(wins) + len(losses)) * 100) if (wins or losses) else None
        stats.avg_return_pct = _avg([trade.result_pct for trade in pct_trades if trade.result_pct is not None])
        stats.avg_win_pct = _avg([trade.result_pct for trade in wins if trade.result_pct is not None])
        stats.avg_loss_pct = _avg([trade.result_pct for trade in losses if trade.result_pct is not None])
        stats.stop_out_rate = (sum(1 for trade in trades if trade.close_reason == "stop") / len(trades) * 100) if trades else None
        stats.trim_rate = (sum(1 for trade in trades if trade.trim_count > 0) / len(trades) * 100) if trades else None
        stats.best_trade = max(pct_trades, key=lambda trade: trade.result_pct or -10**9, default=None)
        stats.worst_trade = min(pct_trades, key=lambda trade: trade.result_pct or 10**9, default=None)

        breakdown: dict[str, AssetStats] = {}
        for asset in ("option", "stock", "future", "unknown"):
            asset_trades = [trade for trade in trades if trade.asset_type == asset]
            asset_pct = [trade.result_pct for trade in asset_trades if trade.result_pct is not None]
            if asset_trades:
                breakdown[asset] = AssetStats(
                    closed=len(asset_trades),
                    wins=sum(1 for trade in asset_trades if trade.result_pct is not None and trade.result_pct > 0),
                    losses=sum(
                        1
                        for trade in asset_trades
                        if (trade.result_pct is not None and trade.result_pct < 0) or (trade.result_text or "").lower() == "x"
                    ),
                    avg_return_pct=_avg(asset_pct),
                )
        stats.asset_breakdown = breakdown

    return sorted(stats_by_id.values(), key=lambda item: (item.closed_trades, item.wins), reverse=True)


def format_pct(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "N/A"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def format_trade_result(trade: Optional[TradeRecord]) -> str:
    if not trade:
        return "N/A"
    result = trade.result_text or format_pct(trade.result_pct)
    return f"{trade.symbol} {result}"
