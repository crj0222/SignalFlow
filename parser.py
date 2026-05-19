import re
from datetime import datetime
from typing import Optional

from models import ParsedAlert


ENTRY_WORDS = (
    "ADD",
    "ADDED",
    "ADDING",
    "BTO",
    "BUY",
    "BUYING",
    "BOUGHT",
    "ENTER",
    "ENTERED",
    "ENTERING",
    "ENTRY",
    "FILL",
    "FILLED",
    "FOLLOWING",
    "GRAB",
    "GRABBED",
    "GRABBING",
    "IN",
    "LONG",
    "OPEN",
    "OPENED",
    "OPENING",
    "POSITION",
    "STARTER",
    "STARTING",
    "TAKE",
    "TAKEN",
    "TAKING",
    "TOOK",
)
EXIT_WORDS = (
    "ALL OUT",
    "CLOSE",
    "CLOSED",
    "CLOSING",
    "EXIT",
    "EXITED",
    "EXITING",
    "OUT",
    "SCALE",
    "SCALE OUT",
    "SCALED",
    "SCALING OUT",
    "SELL",
    "SELLING",
    "SOLD",
    "STC",
    "TRIM",
    "TRIMMED",
    "TRIMMING",
)
STOP_WORDS = (
    "CUT",
    "CUTTING",
    "CUT HERE",
    "INVALIDATED",
    "SL",
    "SL HIT",
    "STOP",
    "STOP HIT",
    "STOP LOSS",
    "STOPPED",
    "STOPPED OUT",
    "STOPS HIT",
)
SOFT_IGNORE_WORDS = (
    "ALERT ANY ENTRY",
    "EYEING",
    "IDEA",
    "INTERESTING",
    "LOOKING AT",
    "LOOKING FOR",
    "MAYBE",
    "NOT IN",
    "PLAN",
    "POSSIBLE",
    "POTENTIAL",
    "RADAR",
    "SETUP",
    "WAITING",
    "WATCH",
    "WATCHING",
    "WILL ALERT",
)

TICKER_RE = re.compile(r"\$?\b([A-Z]{1,5})\b")
CONTRACT_RE = re.compile(r"\b(\d{1,5}(?:\.\d{1,2})?\s*(?:[CP]|CALLS?|PUTS?))\b", re.IGNORECASE)
EXPIRATION_RE = re.compile(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b")
PRICE_PATTERNS = (
    re.compile(
        r"(?:^|\n)\s*(?:ENTRY|FILL(?:ED)?|PRICE|AVG|AVERAGE)\s*:\s*\$?((?:\d+)?\.\d{1,2}|\d+)",
        re.IGNORECASE,
    ),
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
    "OPEN",
    "OPENING",
    "SELL",
    "STC",
    "TRIM",
    "TRIMMING",
    "SCALE",
    "OUT",
    "EXIT",
    "CLOSE",
    "CUT",
    "HERE",
    "ALL",
    "CALL",
    "PUT",
    "DAY",
    "TRADE",
    "SCALP",
    "SCALPING",
    "LOTTO",
    "LIGHT",
    "SWING",
    "OPTION",
    "NOTES",
    "WATCH",
    "WATCHING",
    "IDEA",
    "POSSIBLE",
    "MAYBE",
    "PLAN",
    "SETUP",
    "RADAR",
    "OPEN",
    "OPENING",
    "POSITION",
    "LONG",
    "AT",
    "FOR",
    "THE",
    "AND",
    "I",
    "NOT",
    "IN",
}


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def _parse_ticker(text: str) -> Optional[str]:
    option_line = re.search(r"\bOPTION\s*:\s*([A-Z]{1,5})\b", text, flags=re.IGNORECASE)
    if option_line:
        return option_line.group(1).upper()

    contract_context = re.search(
        r"\$?\b([A-Z]{1,5})\b\s+\$?\d{1,5}(?:\.\d{1,2})?\s*(?:[CP]|CALLS?|PUTS?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if contract_context:
        return contract_context.group(1).upper()

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
    if re.search(r"\b(HALF SIZE|HALF SIZED|1/2 SIZE|1/2 SIZED|HALF POS(?:ITION)?)\b", upper):
        notes.append("Half Size")
    if re.search(r"\b(LIGHT|SMALL|SMALL SIZE|STARTER|STARTER SIZE|SMALLER SIZE)\b", upper):
        notes.append("Light")
    if re.search(r"\b(LOTTO|LOTTERY)\b", upper):
        notes.append("Lotto")
    if re.search(r"\b(SWING|SWINGER|OVERNIGHT|MULTI[- ]?DAY)\b", upper):
        notes.append("Swing")
    if re.search(r"\b(DAY TRADE|DAYTRADE|SCALP|SCALPING|INTRADAY)\b", upper):
        notes.append("Day Trade")

    return " / ".join(notes) if notes else ""


def parse_price(text: str, contract: Optional[str] = None) -> Optional[float]:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            return float(match.group(1))

    # Common shorthand: "BTO SPY 530C .95" or "SPY 530C 5/24 1.20".
    if contract:
        contract_match = re.search(rf"{re.escape(contract)}s?", text, flags=re.IGNORECASE)
        if not contract_match and len(contract) > 1:
            spaced_contract = rf"{re.escape(contract[:-1])}\s*{re.escape(contract[-1])}"
            contract_match = re.search(rf"{spaced_contract}s?", text, flags=re.IGNORECASE)
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
    contract = normalize_contract(contract_match.group(1)) if contract_match else None
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
        action = "trim" if _contains_any(upper, ("TRIM", "TRIMMED", "TRIMMING", "SCALE", "SCALED", "SCALE OUT", "SCALING OUT")) else "exit"
    else:
        return None

    confidence = "normal"
    if action in {"trim", "exit", "stop"} and not (ticker or contract_match):
        confidence = "possible"

    # Avoid routing generic chat like "I entered..." when no tradable details were found.
    if action == "entry" and not has_clean_trade_details and not (ticker and (contract or price is not None)):
        return None

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


def normalize_contract(value: str) -> str:
    cleaned = value.upper().replace("$", "").replace(" ", "")
    cleaned = cleaned.replace("CALLS", "C").replace("CALL", "C")
    cleaned = cleaned.replace("PUTS", "P").replace("PUT", "P")
    return cleaned
