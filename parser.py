import re
from datetime import datetime
from typing import Optional

from models import ParsedAlert


ENTRY_WORDS = ("BUY", "BUYING", "BOUGHT", "BTO", "ENTRY", "ENTER", "ENTERING", "ENTERED", "TAKING", "TOOK", "GRABBED", "FILLED")
EXIT_WORDS = ("SELL", "STC", "TRIM", "TRIMMING", "SCALE OUT", "EXIT", "CLOSE")
STOP_WORDS = ("STOP", "STOPPED", "STOPPED OUT", "STOP LOSS", "SL HIT", "STOP HIT")
SOFT_IGNORE_WORDS = ("WATCHING", "POSSIBLE", "MAYBE", "NOT IN", "LOOKING AT")

TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")
CONTRACT_RE = re.compile(r"\b(\d{1,5}(?:\.\d{1,2})?[CP])\b", re.IGNORECASE)
EXPIRATION_RE = re.compile(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b")
PRICE_PATTERNS = (
    re.compile(
        r"(?:@|\bat\b|\bfor\b|\bpaid\b|\bpaying\b|\bavg\b|\baverage\b|\bentry\b|\bfill(?:ed)?\b|\bstarter\b|\bdebit\b|\bhere\b|\bnow\b|\bin\b|\badding\b|\badd\b)\s*\$?((?:\d+)?\.\d{1,2}|\d+)",
        re.IGNORECASE,
    ),
    re.compile(r"\$((?:\d+)?\.\d{1,2})", re.IGNORECASE),
)

COMMON_NON_TICKERS = {
    "BUY",
    "BUYING",
    "BOUGHT",
    "BTO",
    "ENTRY",
    "ENTER",
    "ENTERING",
    "ENTERED",
    "TAKING",
    "TOOK",
    "GRABBED",
    "FILLED",
    "SELL",
    "STC",
    "TRIM",
    "TRIMMING",
    "SCALE",
    "OUT",
    "EXIT",
    "CLOSE",
    "CALL",
    "PUT",
    "AT",
    "FOR",
    "THE",
    "AND",
    "NOT",
    "IN",
}


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def _parse_ticker(text: str) -> Optional[str]:
    for match in TICKER_RE.finditer(text):
        value = match.group(1).upper()
        if value not in COMMON_NON_TICKERS and not re.fullmatch(r"\d+", value):
            return value
    return None


def today_expiration() -> str:
    now = datetime.now()
    return f"{now.month}/{now.day}"


def parse_trade_note(text: str) -> str:
    upper = text.upper()
    notes = []
    if re.search(r"\b(HALF SIZE|1/2 SIZE|HALF POS(?:ITION)?)\b", upper):
        notes.append("Half Size")
    if re.search(r"\b(LOTTO|LOTTERY)\b", upper):
        notes.append("Lotto")
    if re.search(r"\b(SWING|SWINGER|OVERNIGHT)\b", upper):
        notes.append("Swing")
    if re.search(r"\b(DAY TRADE|DAYTRADE|SCALP|SCALPING)\b", upper):
        notes.append("Day Trade")

    return " / ".join(notes) if notes else "Day Trade"


def parse_price(text: str, contract: Optional[str] = None) -> Optional[float]:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            return float(match.group(1))

    # Common shorthand: "BTO SPY 530C .95" or "SPY 530C 5/24 1.20".
    if contract:
        contract_match = re.search(rf"{re.escape(contract)}s?", text, flags=re.IGNORECASE)
        if contract_match:
            nearby = text[contract_match.end() : contract_match.end() + 40]
            decimal_match = re.search(r"(?<!\d)(?:[-–—:]\s*)?((?:\d+)?\.\d{1,2}|\d+)(?!\d)", nearby)
            if decimal_match:
                return float(decimal_match.group(1))

    decimal_matches = re.findall(r"(?<![\d/])((?:\d+)?\.\d{1,2})(?![\d/])", text)
    if len(decimal_matches) == 1:
        return float(decimal_matches[0])

    return None


def parse_alert(content: str) -> Optional[ParsedAlert]:
    raw = content.strip()
    if not raw:
        return None

    upper = raw.upper()
    has_entry = _contains_any(upper, ENTRY_WORDS)
    has_exit = _contains_any(upper, EXIT_WORDS)
    has_stop = _contains_any(upper, STOP_WORDS)
    has_soft_ignore = _contains_any(upper, SOFT_IGNORE_WORDS)
    contract_match = CONTRACT_RE.search(upper)
    expiration_match = EXPIRATION_RE.search(upper)
    contract = contract_match.group(1).upper() if contract_match else None
    price = parse_price(raw, contract)
    ticker = _parse_ticker(upper)
    has_clean_trade_details = bool(ticker and contract and price is not None)

    # Watchlist-style posts are ignored unless the analyst uses a clear fill/entry word.
    if has_soft_ignore and not has_entry:
        return None

    if has_entry or has_clean_trade_details:
        action = "entry"
    elif has_stop:
        action = "stop"
    elif has_exit:
        action = "trim" if _contains_any(upper, ("TRIM", "TRIMMING", "SCALE OUT")) else "exit"
    else:
        return None

    confidence = "normal"
    if action in {"trim", "exit", "stop"} and not (ticker or contract_match):
        confidence = "possible"

    return ParsedAlert(
        action=action,
        confidence=confidence,
        ticker=ticker,
        contract=contract,
        expiration=expiration_match.group(1) if expiration_match else today_expiration(),
        price=price,
        raw_text=raw,
        trade_note=parse_trade_note(raw),
    )


def estimated_contract_cost(price: Optional[float]) -> str:
    if price is None:
        return "Unknown"
    return f"${price * 100:,.0f}"
