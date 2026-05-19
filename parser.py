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
    "LONG",
    "OPEN",
    "OPENED",
    "OPENING",
    "POSITION",
    "RELOAD",
    "RELOADED",
    "RE-LOADED",
    "REPOSITION",
    "REPOSITIONING",
    "ROLL",
    "ROLLED",
    "STARTER",
    "STARTING",
    "STO",
    "TAKING",
    "TOOK",
)
ADD_WORDS = (
    "ADD",
    "ADDED",
    "ADDING",
    "ADD MORE",
    "AVERAGE",
    "AVERAGED",
    "AVERAGING",
    "AVERAGE DOWN",
    "AVERAGING DOWN",
    "AVERAGE UP",
    "AVERAGING UP",
    "BACK IN",
    "RELOAD",
    "RELOADED",
    "RELOADING",
)
ROLL_WORDS = (
    "ROLL",
    "ROLLED",
    "ROLLING",
    "ROLL BACK",
    "ROLL FORWARD",
    "ROLL OUT",
    "ROLL THESE",
    "ROLLING THESE",
)
EXIT_WORDS = (
    "ALL OUT",
    "AT EVEN",
    "BREAK EVEN",
    "BREAKEVEN",
    "CLOSE HERE",
    "CLOSE POSITION",
    "CLOSE REMAINING",
    "CLOSE RUNNER",
    "CLOSE RUNNERS",
    "DOWN TO A RUNNER",
    "DOWN TO RUNNER",
    "CLOSED",
    "CLOSING",
    "EXIT",
    "EXITED",
    "EXITING",
    "LOCK PROFITS",
    "LOCKING PROFITS",
    "PROFIT TAKING",
    "REDUCE RISK",
    "REDUCED RISK",
    "RUNNER LEFT",
    "SCALE OUT",
    "SCALED",
    "SCALING OUT",
    "SECURE PROFITS",
    "SECURE SOME PROFITS",
    "SELL",
    "SELLING",
    "SOLD",
    "STC",
    "TAKE A TRIM",
    "TAKE PROFIT",
    "TAKE PROFITS",
    "TAKE SOME PROFITS",
    "TAKING PROFIT",
    "TAKING PROFITS",
    "TRIM",
    "TRIMMED",
    "TRIMMING",
)
STOP_WORDS = (
    "CUT",
    "CUTTING",
    "CUT HERE",
    "INVALIDATED",
    "SL HIT",
    "STOP HIT",
    "STOP LOSS HIT",
    "STOP OUT",
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
    "WILL ADD",
    "WILL ALERT",
)
FORWARD_ENTRY_WORDS = (
    "LOOKING TO ENTER",
    "MAY CONSIDER",
    "MIGHT CONSIDER",
    "NEAR OPEN",
    "POTENTIAL ENTRY",
    "WATCHLIST",
)

TICKER_RE = re.compile(r"\$?\b([A-Z]{1,5})\b")
CONTRACT_RE = re.compile(r"(?<![\d/])\$?(\d{1,5}(?:\.\d{1,2})?\s*(?:[CP]|CALLS?|PUTS?))\b", re.IGNORECASE)
EXPIRATION_RE = re.compile(r"\b(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b")
MONTH_EXPIRATION_RE = re.compile(
    r"\b(JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUN(?:E)?|JUL(?:Y)?|AUG(?:UST)?|SEP(?:T(?:EMBER)?)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)\s+(\d{1,2})\b",
    re.IGNORECASE,
)
PRICE_PATTERNS = (
    re.compile(
        r"(?:^|[\n|])\s*(?:ENTRY|FILL(?:ED)?|PRICE|AVG|AVERAGE)\s*[:.,-]?\s*\$?((?:\d+)?\.\d{1,2}|\d+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:@|\bat\b|\bpaid\b|\bpaying\b|\bavg\b|\baverage\b|\bentry\b|\bfill(?:ed)?\b|\bstarter\b|\bdebit\b|\bhere\b|\bnow\b|\badding\b|\badd\b)\s*[:,-]?\s*\$?((?:\d+)?\.\d{1,2}|\d+)",
        re.IGNORECASE,
    ),
    re.compile(r"\$((?:\d+)?\.\d{1,2})", re.IGNORECASE),
)
EXIT_RANGE_RE = re.compile(r"(?<![\d/])((?:\d+)?\.\d{1,2})\s*[-\u2013\u2014]\s*((?:\d+)?\.\d{1,2})(?![\d/])")
REDUCTION_TRIM_RE = re.compile(
    r"\b(?:DOWN TO|REDUCED TO|CUT TO)\s+(?:\d+/\d+|\d{1,3}%|RUNNERS?|A RUNNER)\s+(?:POS(?:ITION)?|SIZE|RUNNERS?)?\b",
    re.IGNORECASE,
)
GAIN_PERCENT_RE = re.compile(r"([+-]?\d{1,4}(?:\.\d+)?)\s*%")
ROLL_COST_RE = re.compile(
    r"(?:\bFOR\b|\bAT\b|@)?\s*\$?((?:\d+)?\.\d{1,2}|\d+)\s*(DEBIT|CREDIT)\b|\b(DEBIT|CREDIT)\s*\$?((?:\d+)?\.\d{1,2}|\d+)",
    re.IGNORECASE,
)
MONTHS = {
    "JAN": 1,
    "JANUARY": 1,
    "FEB": 2,
    "FEBRUARY": 2,
    "MAR": 3,
    "MARCH": 3,
    "APR": 4,
    "APRIL": 4,
    "MAY": 5,
    "JUN": 6,
    "JUNE": 6,
    "JUL": 7,
    "JULY": 7,
    "AUG": 8,
    "AUGUST": 8,
    "SEP": 9,
    "SEPT": 9,
    "SEPTEMBER": 9,
    "OCT": 10,
    "OCTOBER": 10,
    "NOV": 11,
    "NOVEMBER": 11,
    "DEC": 12,
    "DECEMBER": 12,
}

COMMON_NON_TICKERS = {
    "A",
    "ADD",
    "ADDED",
    "ALERT",
    "ALERTS",
    "ALL",
    "AM",
    "AND",
    "ARE",
    "AT",
    "AVERAGE",
    "AVG",
    "BACK",
    "BEARS",
    "BEFORE",
    "BISHOP",
    "BOUGHT",
    "BTC",
    "BTO",
    "BULLS",
    "BUY",
    "BUYING",
    "CALL",
    "COBAIN",
    "CLOSE",
    "CUT",
    "DAY",
    "DEMON",
    "DOWN",
    "DMA",
    "ENTER",
    "ENTERED",
    "ENTERING",
    "ENTRY",
    "ES",
    "EVEN",
    "EXIT",
    "EXPO",
    "FILLED",
    "FOMC",
    "FOR",
    "FROM",
    "GRABBED",
    "HERE",
    "HIGH",
    "HOD",
    "I",
    "IDEA",
    "IF",
    "IM",
    "IN",
    "IS",
    "IT",
    "LIGHT",
    "LOD",
    "LONG",
    "LONGS",
    "LOTTO",
    "MAYBE",
    "ME",
    "MORE",
    "MY",
    "NOT",
    "NOTES",
    "NQ",
    "OF",
    "OK",
    "ON",
    "OPEN",
    "OPENING",
    "OPTION",
    "OR",
    "OUR",
    "OUT",
    "PING",
    "PLAN",
    "PLANNED",
    "PLAYS",
    "POC",
    "POSITION",
    "POSITIONS",
    "POSSIBLE",
    "PURE",
    "PUT",
    "RADAR",
    "RELOAD",
    "RELOADED",
    "RISK",
    "RISKIER",
    "ROLLED",
    "SCALP",
    "SCALPING",
    "SCOTT",
    "SELL",
    "SETUP",
    "SHORT",
    "SHORTS",
    "SHYAMAL",
    "STC",
    "SWING",
    "TAKING",
    "THAT",
    "THE",
    "THESE",
    "THIS",
    "THOSE",
    "TO",
    "TOOK",
    "TRADE",
    "TRIM",
    "TRIMMING",
    "UNDER",
    "VIX",
    "WATCH",
    "WATCHING",
    "WAXUI",
    "WE",
    "WITH",
    "WITHOUT",
    "YOU",
    "YOUR",
}


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def _normalize_ticker_candidate(value: str) -> str:
    value = value.upper()
    if re.fullmatch(r"SPX{2,}", value):
        return "SPX"
    if re.fullmatch(r"SPY{2,}", value):
        return "SPY"
    return value


def _strip_alert_noise(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", " ", text)
    cleaned = re.sub(r"<@&?\d+>|<#\d+>|@everyone|@here", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"@[A-Z#_][A-Z0-9_.#]*(?:\s+(?:ALERTS?|PING|OPTIONS?|PLAYS?|ROLE))?",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def _parse_ticker(text: str) -> Optional[str]:
    text = _strip_alert_noise(text)

    option_line = re.search(r"\bOPTION\s*:\s*\$?([A-Z]{1,5})\b", text, flags=re.IGNORECASE)
    if option_line:
        return option_line.group(1).upper()

    for match in re.finditer(r"\$([A-Z]{1,5})\b", text):
        value = _normalize_ticker_candidate(match.group(1))
        if value not in COMMON_NON_TICKERS:
            return value

    here_context = re.search(r"\b([A-Z]{1,5})\b\s+(?:HERE|CALLS?|PUTS?|CONTRACTS?)\b", text, flags=re.IGNORECASE)
    if here_context:
        value = _normalize_ticker_candidate(here_context.group(1))
        if value not in COMMON_NON_TICKERS:
            return value

    repeated_index = re.search(r"\b(SPX{2,}|SPY{2,})\b", text, flags=re.IGNORECASE)
    if repeated_index:
        return _normalize_ticker_candidate(repeated_index.group(1))

    contract_context = re.search(
        r"\$?\b([A-Z]{1,5})\b\s+(?:\d{1,2}/\d{1,2}\s+)?\$?\d{1,5}(?:\.\d{1,2})?\s*(?:[CP]|CALLS?|PUTS?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if contract_context:
        value = _normalize_ticker_candidate(contract_context.group(1))
        if value not in COMMON_NON_TICKERS:
            return value

    for match in TICKER_RE.finditer(text):
        value = _normalize_ticker_candidate(match.group(1))
        if value not in COMMON_NON_TICKERS and not re.fullmatch(r"\d+", value):
            return value
    return None


def today_expiration() -> str:
    now = datetime.now()
    return f"{now.month}/{now.day}"


def parse_expiration(text: str) -> Optional[str]:
    for match in EXPIRATION_RE.finditer(text):
        before = text[max(0, match.start() - 16) : match.start()].lower()
        after = text[match.end() : match.end() + 24].lower()
        if re.search(r"(about|down to|to about|holding)\s*$", before) and re.search(r"^\s*(position|pos|runner|left)?\b", after):
            continue
        if re.search(r"^\s*(position|pos)\b", after):
            continue
        return match.group(1)

    month_match = MONTH_EXPIRATION_RE.search(text)
    if month_match:
        month = MONTHS[month_match.group(1).upper()]
        day = int(month_match.group(2))
        return f"{month}/{day}"

    return None


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
    if re.search(r"\b(0DTE|0 DTE|ZERO DTE)\b", upper):
        notes.append("0DTE")
    if re.search(r"\b(DAY TRADE|DAYTRADE|SCALP|SCALPING|INTRADAY)\b", upper):
        notes.append("Day Trade")

    return " / ".join(notes) if notes else ""


def parse_price(text: str, contract: Optional[str] = None) -> Optional[float]:
    for pattern in PRICE_PATTERNS:
        match = pattern.search(text)
        if match:
            price = float(match.group(1))
            if price <= 100:
                return price

    # Common shorthand: "BTO SPY 530C .95" or "SPY 530C 5/24 1.20".
    if contract:
        contract_match = re.search(rf"{re.escape(contract)}s?", text, flags=re.IGNORECASE)
        if not contract_match and len(contract) > 1:
            spaced_contract = rf"\$?{re.escape(contract[:-1])}\s*{re.escape(contract[-1])}"
            contract_match = re.search(rf"{spaced_contract}s?", text, flags=re.IGNORECASE)
        if contract_match:
            nearby = text[contract_match.end() : contract_match.end() + 40]
            decimal_match = re.search(r"(?<!\d)(?:[-\u2013\u2014:]\s*)?((?:\d+)?\.\d{1,2}|\d+)(?!\d)", nearby)
            if decimal_match:
                price = float(decimal_match.group(1))
                if price <= 100:
                    return price

    decimal_matches = re.findall(r"(?<![\d/])((?:\d+)?\.\d{1,2})(?![\d/])", text)
    if len(decimal_matches) == 1:
        return float(decimal_matches[0])

    return None


def parse_exit_price(text: str) -> Optional[float]:
    range_match = EXIT_RANGE_RE.search(text)
    if range_match:
        return float(range_match.group(2))
    return parse_price(text)


def parse_gain_percent(text: str) -> Optional[float]:
    matches = [float(match.group(1)) for match in GAIN_PERCENT_RE.finditer(text)]
    if not matches:
        return None
    positive = [value for value in matches if value > 0]
    return positive[-1] if positive else matches[-1]


def _contract_expiration_near(text: str, match: re.Match[str]) -> Optional[str]:
    before = text[max(0, match.start() - 28) : match.start()]
    after = text[match.end() : match.end() + 18]
    before_matches = EXPIRATION_RE.findall(before)
    if before_matches:
        return before_matches[-1]
    return parse_expiration(after)


def parse_roll_cost(text: str) -> tuple[Optional[float], Optional[str]]:
    match = ROLL_COST_RE.search(text)
    if not match:
        return None, None
    if match.group(2):
        return float(match.group(1)), match.group(2).lower()
    return float(match.group(4)), match.group(3).lower()


def parse_roll_details(text: str) -> dict[str, Optional[object]]:
    contracts = list(CONTRACT_RE.finditer(text))
    old_contract = None
    old_expiration = None
    new_contract = None
    new_expiration = None

    if contracts:
        new_match = contracts[-1]
        new_contract = normalize_contract(new_match.group(1))
        new_expiration = _contract_expiration_near(text, new_match)
        if len(contracts) > 1:
            old_match = contracts[0]
            old_contract = normalize_contract(old_match.group(1))
            old_expiration = _contract_expiration_near(text, old_match)

    roll_cost, roll_cost_type = parse_roll_cost(text)
    new_price = parse_price(text, new_contract) if new_contract else parse_price(text)
    if roll_cost_type and new_price == roll_cost:
        new_price = None
    return {
        "old_contract": old_contract,
        "old_expiration": old_expiration,
        "new_contract": new_contract,
        "new_expiration": new_expiration,
        "new_price": new_price,
        "roll_cost": roll_cost,
        "roll_cost_type": roll_cost_type,
    }


def _has_strong_entry(text: str, has_clean_trade_details: bool) -> bool:
    if re.search(
        r"(?:^|[\n|])\s*(?:OPEN|BTO|STO|BUY(?:ING)?|BOUGHT|ENTER(?:ING|ED)?|I'?M ENTERING|TAKING(?!\s+PROFITS?\b)|TOOK(?!\s+PROFITS?\b)|GRABB(?:ED|ING)?|FILL(?:ED)?)\b",
        text,
    ):
        return True
    return bool(has_clean_trade_details and re.search(r"\b(HERE|AVG|AVERAGE)\b", text))


def _has_forward_add_context(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:MAY|MIGHT|WILL|WOULD|CAN|COULD|LOOKING TO|ROOM TO|LEAV(?:E|ING) ROOM TO|WAIT(?:ING)? TO)\s+ADD\b",
            text,
        )
        or re.search(r"\bADD(?:ING)?\s+(?:IF|ON A|ON THE|UNDER|OVER|AT SUPPORT)\b", text)
    )


def _has_strong_add(text: str, price: Optional[float], contract_match: Optional[re.Match[str]]) -> bool:
    if _has_forward_add_context(text):
        return False
    if re.search(r"\bAVERAG(?:E|ED|ING)(?:\s+(?:DOWN|UP))?\b", text):
        return True
    if re.search(r"\b(?:ADD(?:ED|ING)?|RELOAD(?:ED|ING)?|BACK IN)\b", text):
        return bool(price is not None or contract_match or re.search(r"\bHERE\b", text))
    return False


def _looks_like_runner_trim(text: str) -> bool:
    return bool(
        REDUCTION_TRIM_RE.search(text)
        or (EXIT_RANGE_RE.search(text) and re.search(r"\b(HOLDING|RUNNER|RUNNERS|MOST|MAJORITY|REDUCED RISK)\b", text))
    )


def parse_alert(content: str) -> Optional[ParsedAlert]:
    raw = content.strip()
    if not raw:
        return None

    cleaned = _strip_alert_noise(raw)
    upper = cleaned.upper()
    has_entry = _contains_any(upper, ENTRY_WORDS)
    has_roll = _contains_any(upper, ROLL_WORDS)
    has_exit = _contains_any(upper, EXIT_WORDS)
    has_stop = _contains_any(upper, STOP_WORDS)
    has_soft_ignore = _contains_any(upper, SOFT_IGNORE_WORDS)
    contract_match = CONTRACT_RE.search(upper)
    contract = normalize_contract(contract_match.group(1)) if contract_match else None
    expiration = parse_expiration(cleaned)
    ticker = _parse_ticker(upper)
    price = parse_price(cleaned, contract)
    has_clean_trade_details = bool(ticker and contract and price is not None)
    has_strong_entry = _has_strong_entry(upper, has_clean_trade_details)
    has_strong_add = _has_strong_add(upper, price, contract_match)
    has_runner_trim = _looks_like_runner_trim(upper)

    # Watchlist-style posts are ignored unless the analyst uses a clear fill/entry word.
    if has_soft_ignore and not (has_strong_entry or has_strong_add or has_roll or has_exit or has_stop):
        return None

    # Exit/stop words win over broad entry words, so "taking a trim" is not routed as a new entry.
    roll_details = parse_roll_details(cleaned) if has_roll else {}
    if has_roll and (roll_details.get("new_contract") or contract_match):
        action = "roll_option"
        contract = roll_details.get("new_contract") or contract
        expiration = roll_details.get("new_expiration") or expiration
        price = roll_details.get("new_price")
    elif has_stop:
        action = "close"
    elif has_exit or has_runner_trim:
        action = "trim" if _contains_any(upper, ("TRIM", "TRIMMED", "TRIMMING", "SCALED", "SCALE OUT", "SCALING OUT")) else "close"
        if has_runner_trim and not has_exit:
            action = "trim"
        price = parse_exit_price(cleaned)
    elif has_strong_add:
        if re.search(r"\bAVERAG(?:E|ED|ING)\s+DOWN\b", upper):
            action = "average_down"
        elif re.search(r"\bAVERAG(?:E|ED|ING)\s+UP\b", upper):
            action = "average_up"
        else:
            action = "add"
    elif has_strong_entry:
        action = "entry"
    elif has_entry or has_clean_trade_details:
        action = "entry"
    else:
        return None

    confidence = "high"
    if action in {"trim", "close", "exit", "stop"} and not (ticker or contract_match):
        confidence = "medium"
    if action in {"add", "average_down", "average_up"} and (price is None or not (ticker or contract_match)):
        confidence = "medium"
    if action == "roll_option" and not (contract and (expiration or price is not None)):
        confidence = "medium"

    # Avoid routing generic chat like "I entered..." when no tradable details were found.
    if action == "entry" and not has_clean_trade_details and not (ticker and (contract or price is not None)):
        return None
    if action == "entry" and price is None and _contains_any(upper, FORWARD_ENTRY_WORDS):
        return None
    if action in {"add", "average_down", "average_up"} and _has_forward_add_context(upper):
        return None

    return ParsedAlert(
        action=action,
        confidence=confidence,
        ticker=ticker,
        contract=contract,
        expiration=expiration if expiration else today_expiration(),
        price=price,
        raw_text=raw,
        trade_note=parse_trade_note(raw),
        old_contract=roll_details.get("old_contract") if roll_details else None,
        old_expiration=roll_details.get("old_expiration") if roll_details else None,
        roll_cost=roll_details.get("roll_cost") if roll_details else None,
        roll_cost_type=roll_details.get("roll_cost_type") if roll_details else None,
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
