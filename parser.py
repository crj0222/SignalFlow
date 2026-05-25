import re
from datetime import datetime, timedelta
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
    "DOUBLE DOWN",
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
    "AT B/E",
    "AT BE",
    "B/E",
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
    "FLAT",
    "FLAT ON",
    "DONE WITH THIS TRADE",
    "LOCK PROFITS",
    "LOCK SOME PROFITS",
    "LOCKING PROFITS",
    "LOCKING SOME PROFITS",
    "OUT HERE",
    "PROFIT TAKING",
    "REDUCE RISK",
    "REDUCED RISK",
    "RUNNER LEFT",
    "RUNNERS LEFT",
    "RISK FREE",
    "RISK-FREE",
    "SCALE OUT",
    "SCALED",
    "SCALING OUT",
    "SECURE PROFITS",
    "SECURE SOME PROFITS",
    "SELL",
    "SELLING",
    "SOLD",
    "STC",
    "TAKE HALF",
    "TAKE SOME OFF",
    "TAKE A TRIM",
    "TAKE PROFIT",
    "TAKE PROFITS",
    "TAKE SOME PROFITS",
    "TOOK SOME PROFIT",
    "TOOK SOME PROFITS",
    "TOOK SOME OFF",
    "TAKING PROFIT",
    "TAKING PROFITS",
    "TAKING HALF",
    "TOOK PROFIT",
    "TOOK PROFITS",
    "HALF OFF",
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
    "TAKING THE LOSS",
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
FUTURES_RE = re.compile(r"(?<![A-Z0-9])/?(MES|MNQ|M2K|MYM|ES|NQ|RTY|YM|CL|GC|SI|HG|NG|ZB|ZN|ZF|ZT)\b", re.IGNORECASE)
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
TRIM_PRICE_RE = re.compile(
    r"(?:^|[\n|])\s*\$?((?:\d+)?\.\d{1,2}|\d+)\s*[-\u2013\u2014]\s*(?:TRIM(?:MED)?|SCALE(?:D)?|SELL|STC|TAKE\s+PROFITS?)\b",
    re.IGNORECASE,
)
TRIM_SIZE_PRICE_RE = re.compile(
    r"(?:^|[\n|])\s*\$?((?:\d+)?\.\d{1,2}|\d+)\s*[-\u2013\u2014]\s*(?:\d{1,3})\s*%",
    re.IGNORECASE,
)
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
    "B",
    "BE",
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
    "DONE",
    "ENTER",
    "ENTERED",
    "ENTERING",
    "ENTRY",
    "ES",
    "EVEN",
    "EXIT",
    "EXPO",
    "FILLED",
    "FLAT",
    "FOMC",
    "FOR",
    "FREE",
    "FROM",
    "FULL",
    "FYI",
    "GRABBED",
    "HALF",
    "HERE",
    "HIGH",
    "HIT",
    "HOD",
    "I",
    "IDEA",
    "IF",
    "IMO",
    "IM",
    "IN",
    "IS",
    "IT",
    "LIGHT",
    "LEFT",
    "LOD",
    "LONG",
    "LONGS",
    "LOSS",
    "LOT",
    "LOTTO",
    "MAY",
    "MAYBE",
    "ME",
    "MORE",
    "MY",
    "NEXT",
    "NICE",
    "NOT",
    "NOW",
    "NOTES",
    "NQ",
    "OF",
    "OFF",
    "OK",
    "ON",
    "ONTO",
    "OPEN",
    "OPENING",
    "OPTION",
    "ONLY",
    "OR",
    "OUR",
    "OUT",
    "PAID",
    "PING",
    "PLAN",
    "PLANNED",
    "PLAYS",
    "POC",
    "POSITION",
    "POSITIONS",
    "POSSIBLE",
    "PRICE",
    "PURE",
    "PUT",
    "RADAR",
    "RELOAD",
    "RELOADED",
    "REST",
    "RISK",
    "RISKIER",
    "ROLLED",
    "RUNNER",
    "RUNNERS",
    "SCALP",
    "SCALPING",
    "SCOTT",
    "SELL",
    "SETUP",
    "SHORT",
    "SHORTS",
    "SHYAMAL",
    "SL",
    "SMALL",
    "SOME",
    "SOLD",
    "STOP",
    "STC",
    "SWING",
    "TAKE",
    "TAKING",
    "THAT",
    "THE",
    "THESE",
    "THIS",
    "THOSE",
    "TICKER",
    "TO",
    "TOOK",
    "TRADE",
    "TRIM",
    "TRIMMING",
    "UNDER",
    "UP",
    "VIX",
    "WATCH",
    "WATCHING",
    "WEEK",
    "WAXUI",
    "WE",
    "WEEK",
    "WITH",
    "WITHOUT",
    "WORK",
    "YOU",
    "YOUR",
}


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def _normalize_ticker_candidate(value: str) -> str:
    value = value.upper().lstrip("/")
    if value in {"MES", "MNQ", "M2K", "MYM", "ES", "NQ", "RTY", "YM", "CL", "GC", "SI", "HG", "NG", "ZB", "ZN", "ZF", "ZT"}:
        return f"/{value}"
    if re.fullmatch(r"SPX{2,}", value):
        return "SPX"
    if re.fullmatch(r"SPY{2,}", value):
        return "SPY"
    return value


def _strip_alert_noise(text: str) -> str:
    cleaned = re.sub(r"https?://\S+", " ", text)
    cleaned = re.sub(r"\b[A-Z][A-Z0-9_]{1,20}'S\b", " ", cleaned, flags=re.IGNORECASE)
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
    text = re.sub(r"\bB\s*/\s*E\b|\bB\.E\.\b", " ", text, flags=re.IGNORECASE)

    futures_match = FUTURES_RE.search(text)
    if futures_match and re.search(r"\b(LONG|SHORT|BUY(?:ING)?|BOUGHT|SELL(?:ING)?|SOLD|ENTER(?:ING|ED)?|OPEN(?:ING)?|ADD(?:ED|ING)?|TRIM(?:MED|MING)?|CLOSE(?:D|ING)?|EXIT(?:ED|ING)?|STOP(?:PED)?|CUT)\b", text, flags=re.IGNORECASE):
        return _normalize_ticker_candidate(futures_match.group(1))

    option_line = re.search(r"\bOPTION\s*:\s*\$?([A-Z]{1,5})\b", text, flags=re.IGNORECASE)
    if option_line:
        return option_line.group(1).upper()

    ticker_line = re.search(r"\bTICKER\s*:\s*\$?([A-Z]{1,5})\b", text, flags=re.IGNORECASE)
    if ticker_line:
        return _normalize_ticker_candidate(ticker_line.group(1))

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


def current_week_friday_expiration() -> str:
    now = datetime.now()
    days_until_friday = (4 - now.weekday()) % 7
    friday = now + timedelta(days=days_until_friday)
    return f"{friday.month}/{friday.day}"


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

    if re.search(r"\b(WEEKLIES|WEEKLY|WKLY|WEEKLYS)\b", text, flags=re.IGNORECASE):
        return current_week_friday_expiration()

    return None


def parse_trade_note(text: str) -> str:
    upper = text.upper()
    notes = []
    if re.search(r"\b(SWING|SWINGER|OVERNIGHT|MULTI[- ]?DAY)\b", upper):
        notes.append("Swing")
    if re.search(r"\bSHORT\b", upper):
        notes.append("Short")
    elif re.search(r"\bLONG\b", upper):
        notes.append("Long")
    if re.search(r"\b(0DTE|0 DTE|ZERO DTE)\b", upper):
        notes.append("0DTE")
    if re.search(r"\b(DAY TRADE|DAYTRADE|SCALP|SCALPING|INTRADAY)\b", upper):
        notes.append("Day Trade")
    if re.search(r"\b(LOTTO|LOTTERY)\b", upper):
        notes.append("Lotto")
    if re.search(r"\b(HALF[-\s]*(?:SIZE|SIZED|POS(?:ITION)?))\b|\b1/2[-\s]*(?:SIZE|SIZED|POS(?:ITION)?)\b", upper):
        notes.append("Half Size")
    if re.search(r"\b1/3[-\s]*(?:SIZE|SIZED|POS(?:ITION)?)\b", upper):
        notes.append("1/3 Size")
    if re.search(r"\b1/4[-\s]*(?:SIZE|SIZED|POS(?:ITION)?)\b", upper):
        notes.append("1/4 Size")
    if re.search(r"\b(LIGHT|SMALL|SMALL SIZE|STARTER|STARTER SIZE|SMALLER SIZE)\b", upper):
        notes.append("Light")

    return " / ".join(notes) if notes else ""


def parse_position_note(text: str) -> str:
    upper = text.upper()
    if re.search(r"(?:^|[\n|])\s*\$?(?:\d+)?\.\d{1,2}\s*[-\u2013\u2014]\s*50\s*%", text, re.IGNORECASE) or re.search(r"\b(?:TRIM(?:MED|MING)?|SELL(?:ING)?|SOLD|TAK(?:E|ING))\s+50\s*%", upper):
        return "Half Position"
    if re.search(r"\b(?:SOLD|SELLING|TRIM(?:MED|MING)?|TAK(?:E|ING))\s+(?:A\s+)?HALF\b|\bHALF\s+(?:OFF|TRIM|POSITION|POS)\b|\b1/2\s+(?:OFF|POSITION|POS|LEFT)\b", upper):
        return "Half Position"
    if re.search(r"\b(?:DOWN TO|REDUCED TO|CUT TO)\s+1/3\b|\b1/3\s+(?:POSITION|POS|LEFT|REMAINING)\b", upper):
        return "1/3 Position"
    if re.search(r"\b(?:DOWN TO|REDUCED TO|CUT TO)\s+1/4\b|\b1/4\s+(?:POSITION|POS|LEFT|REMAINING)\b", upper):
        return "1/4 Position"
    if re.search(r"\b(?:RUNNER|RUNNERS|RUNNER LEFT|RUNNERS LEFT|LEAVING RUNNERS|LETTING RUNNERS|TRAILING RUNNERS|REST RIDE|REST RIDING)\b", upper):
        return "Runners"
    if re.search(r"\b(?:RISK FREE|RISK-FREE|REDUCE RISK|REDUCED RISK|STOP TO EVEN|STOP LOSS TO EVEN|BREAKEVEN|BREAK EVEN|AT EVEN|AT B/E|AT BE|B/E)\b", upper):
        return "Risk Free"
    if re.search(r"\b(?:MOST|MAJORITY)\s+(?:OFF|OUT|SOLD|TRIMMED)\b|\b(?:SOLD|SELLING|TRIM(?:MED|MING)?)\s+(?:MOST|MAJORITY)\b", upper):
        return "Majority Trimmed"
    return ""


def infer_asset_type(ticker: Optional[str], contract: Optional[str], text: str = "") -> str:
    if contract:
        return "option"
    if ticker and ticker.startswith("/"):
        return "future"
    if re.search(r"\b(FUTURES?|CONTRACTS?)\b", text, flags=re.IGNORECASE) and ticker and FUTURES_RE.search(ticker):
        return "future"
    if re.search(r"\b(SHARES?|STOCKS?|COMMONS?|EQUITY)\b", text, flags=re.IGNORECASE):
        return "stock"
    return "unknown"


def apply_default_expiration_and_note(action: str, expiration: Optional[str], trade_note: Optional[str], asset_type: str = "option") -> tuple[Optional[str], str]:
    note = trade_note or ""
    if asset_type != "option":
        return expiration, note
    if expiration:
        return expiration, note
    note_parts = {part.strip() for part in note.split(" / ") if part.strip()}
    has_explicit_duration = bool(note_parts & {"Swing", "0DTE", "Day Trade"})
    if action in {"entry", "add", "average_down", "average_up"} and not has_explicit_duration:
        note = " / ".join([part for part in [note, "Day Trade"] if part])
    return today_expiration(), note


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


def parse_market_price(text: str, ticker: Optional[str] = None) -> Optional[float]:
    if ticker:
        clean_ticker = re.escape(ticker.lstrip("/"))
        ticker_match = re.search(rf"/?{clean_ticker}\b", text, flags=re.IGNORECASE)
        if not ticker_match:
            return None
        nearby = text[ticker_match.end() : ticker_match.end() + 36]
        numbers = re.findall(r"(?<![\d/])\$?(\d{1,6}(?:\.\d{1,2})?)(?![\d/])", nearby)
        if numbers:
            return float(numbers[0])

    patterns = (
        r"(?:@|\bat\b|\bentry\b|\bavg\b|\baverage\b|\bfilled?\b|\bfrom\b)\s*[:,-]?\s*\$?(\d{1,6}(?:\.\d{1,2})?)",
        r"\b(?:LONG|SHORT|BUY(?:ING)?|BOUGHT|ENTER(?:ING|ED)?|OPEN(?:ING)?|ADD(?:ED|ING)?)\b\s+/?[A-Z]{1,5}\s+\$?(\d{1,6}(?:\.\d{1,2})?)\b",
        r"\$?(\d{1,6}(?:\.\d{1,2})?)\s+(?:ENTRY|AVG|AVERAGE|FILL(?:ED)?)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def parse_exit_price(text: str) -> Optional[float]:
    trim_price_matches = [*TRIM_PRICE_RE.finditer(text), *TRIM_SIZE_PRICE_RE.finditer(text)]
    if trim_price_matches:
        trim_price_matches.sort(key=lambda match: match.start())
        return float(trim_price_matches[-1].group(1))

    range_match = EXIT_RANGE_RE.search(text)
    if range_match:
        return float(range_match.group(2))
    return parse_price(text) or parse_market_price(text)


def parse_gain_percent(text: str) -> Optional[float]:
    values = []
    image_context = "[image alert]" in text.lower()
    trim_size_context = bool(TRIM_SIZE_PRICE_RE.search(text))
    for match in GAIN_PERCENT_RE.finditer(text):
        raw_value = match.group(1)
        value = float(match.group(1))
        context = text[max(0, match.start() - 24) : match.end() + 24].lower()
        has_gain_context = raw_value.startswith(("+", "-")) or re.search(
            r"\b(gain|gains|profit|profits|loss|lost|down|red|negative|minus|up)\b",
            context,
        ) or (image_context and not trim_size_context)
        if not has_gain_context:
            continue
        if value > 0 and re.search(r"\b(loss|lost|down|red|negative|minus)\b", context):
            value = -value
        values.append(value)
    if not values:
        return None
    return values[-1]


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
        r"(?:^|[\n|])\s*(?:[^\w$]{0,4}\s*)?(?:OPEN|BTO|STO|BUY(?:ING)?|BOUGHT|ENTER(?:ING|ED)?|I'?M ENTERING|TAKING(?!\s+PROFITS?\b)|TOOK(?!\s+PROFITS?\b)|GRABB(?:ED|ING)?|FILL(?:ED)?|LONG|SHORT)\b",
        text,
    ):
        return True
    return bool(has_clean_trade_details and re.search(r"\b(HERE|AVG|AVERAGE)\b", text))


def _has_stock_context(text: str) -> bool:
    return bool(re.search(r"\b(SHARES?|STOCKS?|COMMONS?|EQUITY)\b", text))


def _has_structured_stock_entry_setup(text: str) -> bool:
    has_ticker = bool(re.search(r"\$[A-Z]{1,5}\b|\bTICKER\s*:", text, re.IGNORECASE))
    has_entry_price = bool(
        re.search(
            r"(?:^|[\n|])\s*(?:[^\w$]{0,5}\s*)?ENTRY\s*[:.,-]\s*\$?\d{2,6}(?:\.\d{1,2})?",
            text,
            re.IGNORECASE,
        )
        or (
            re.search(r"\bNEW\s+ENTRY\s+IDEA\b", text, re.IGNORECASE)
            and re.search(r"(?:^|[\n|])\s*(?:[^\w$]{0,5}\s*)?\$?\d{1,6}(?:\.\d{1,2})?\s*$", text, re.IGNORECASE | re.MULTILINE)
        )
    )
    has_trade_plan_context = bool(
        re.search(
            r"\b(?:SWING|TRADE\s+IDEA|POSITION|POS|LEVELS?|TARGETS?|SL|STOP\s+LOSS|STOP)\b",
            text,
            re.IGNORECASE,
        )
    )
    has_waiting_context = bool(
        re.search(
            r"\b(?:NOT\s+IN|WAIT(?:ING)?|MAY\s+ENTER|MIGHT\s+ENTER|POSSIBLE|WATCH(?:ING)?|IF\s+IT|IF\s+WE|IF\s+THIS)\b",
            text,
            re.IGNORECASE,
        )
    )
    return has_ticker and has_entry_price and has_trade_plan_context and not has_waiting_context


def _has_futures_context(text: str) -> bool:
    return bool(FUTURES_RE.search(text) and re.search(r"\b(LONG|SHORT|FUTURES?|CONTRACTS?|POINTS?)\b", text))


def _has_forward_add_context(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:MAY|MIGHT|WILL|WOULD|CAN|COULD|LOOKING TO|ROOM TO|LEAV(?:E|ING) ROOM TO|WAIT(?:ING)? TO)\s+ADD\b",
            text,
        )
        or re.search(r"\bADD(?:ING)?\s+(?:IF|ON A|ON THE|UNDER|OVER|AT SUPPORT)\b", text)
    )


def _has_forward_entry_context(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:MAY|MIGHT|WILL|WOULD|CAN|COULD|LOOKING TO|WAIT(?:ING)? TO)\s+(?:BUY|ENTER|TAKE|OPEN|LONG|SHORT|GRAB)\b",
            text,
        )
        or re.search(r"\bMAY\s+GO\s+(?:LONG|SHORT)\b", text)
        or re.search(r"\b(?:IF|WHEN)\s+(?:IT\s+)?(?:BREAKS?|HOLDS?|RECLAIMS?|GETS|GOES|PUSHES|TAKES)\b", text)
        or re.search(r"\b(?:OVER|UNDER|ABOVE|BELOW)\s+\$?\d{1,6}(?:\.\d{1,2})?\b", text)
        or _contains_any(text, FORWARD_ENTRY_WORDS)
    )


def _has_strong_add(text: str, price: Optional[float], contract_match: Optional[re.Match[str]]) -> bool:
    if _has_forward_add_context(text):
        return False
    if re.search(r"\bDOUBLE\s+DOWN\b", text):
        return True
    if re.search(r"\b(?:AVG|AVERAG(?:E|ED|ING))(?:\s+(?:DOWN|UP))?\b", text):
        return True
    if re.search(r"\b(?:ADD(?:ED|ING)?|RELOAD(?:ED|ING)?|BACK IN)\b", text):
        return bool(price is not None or contract_match or re.search(r"\bHERE\b", text))
    return False


def _looks_like_runner_trim(text: str) -> bool:
    return bool(
        REDUCTION_TRIM_RE.search(text)
        or TRIM_PRICE_RE.search(text)
        or TRIM_SIZE_PRICE_RE.search(text)
        or re.search(r"\b(?:LEAVING|LETTING|HOLDING)\s+RUNNERS?\b", text)
        or re.search(r"\b(?:LETTING|LET)\s+(?:THE\s+)?REST\s+RIDE\b", text)
        or re.search(r"\b[A-Z]{1,5}\s+RUNNERS?\s+FROM\s+HERE\b", text)
        or re.search(r"\bTAKE\s+\d{1,3}%\s+OFF\b", text)
        or re.search(r"\b(?:RUNNERS?\s+ONLY|ONLY\s+RUNNERS?)\b", text)
        or re.search(r"\b\d{1,5}(?:\.\d{1,2})?\s*(?:[CP]|CALLS?|PUTS?)\b.*\bRUNNERS?\b", text)
        or (EXIT_RANGE_RE.search(text) and re.search(r"\b(HOLDING|RUNNER|RUNNERS|MOST|MAJORITY|REDUCED RISK)\b", text))
    )


def _looks_like_level_trim(text: str) -> bool:
    return bool(
        re.search(r"\bTICKER\s*:\s*\$?[A-Z]{1,5}\b|\$[A-Z]{1,5}\b", text, re.IGNORECASE)
        and re.search(
            r"\b(?:ALL\s+LEVELS?|LEVELS?|TARGETS?|PT)\s*\d*\s*(?:HIT|TAGGED|REACHED|SMACKED)\b",
            text,
            re.IGNORECASE,
        )
        and GAIN_PERCENT_RE.search(text)
    )


def parse_alert(content: str) -> Optional[ParsedAlert]:
    raw = content.strip()
    if not raw:
        return None

    cleaned = _strip_alert_noise(raw)
    upper = cleaned.upper()
    if re.search(r"\bNOTES?\s*/\s*COMMENT\b", upper) and not re.search(r"\bTICKER\s*:", upper):
        return None
    has_entry = _contains_any(upper, ENTRY_WORDS)
    has_roll = _contains_any(upper, ROLL_WORDS)
    has_exit = _contains_any(upper, EXIT_WORDS)
    has_stop = _contains_any(upper, STOP_WORDS)
    has_soft_ignore = _contains_any(upper, SOFT_IGNORE_WORDS)
    contract_match = CONTRACT_RE.search(upper)
    contract = normalize_contract(contract_match.group(1)) if contract_match else None
    expiration = parse_expiration(cleaned)
    ticker = _parse_ticker(upper)
    asset_type = infer_asset_type(ticker, contract, cleaned)
    structured_stock_entry = _has_structured_stock_entry_setup(cleaned) and not contract
    if structured_stock_entry:
        asset_type = "stock"
    price = parse_price(cleaned, contract)
    if structured_stock_entry:
        price = parse_market_price(cleaned, ticker) or price
    if asset_type in {"stock", "future"}:
        price = price if price is not None else parse_market_price(cleaned, ticker)
    has_clean_trade_details = bool(
        ticker
        and price is not None
        and (
            contract
            or asset_type == "future"
            or (asset_type == "stock" and (_has_stock_context(upper) or structured_stock_entry))
        )
    )
    has_strong_entry = _has_strong_entry(upper, has_clean_trade_details) or structured_stock_entry
    has_strong_add = _has_strong_add(upper, price, contract_match)
    has_forward_entry = _has_forward_entry_context(upper)
    has_runner_trim = _looks_like_runner_trim(upper)
    has_level_trim = _looks_like_level_trim(cleaned)

    # Watchlist-style posts are ignored unless the analyst uses a clear fill/entry word.
    if has_soft_ignore and not (has_strong_entry or has_strong_add or has_roll or has_exit or has_stop or has_runner_trim or has_level_trim):
        return None
    if has_forward_entry and not (has_strong_entry or has_strong_add or has_roll or has_exit or has_stop):
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
    elif has_exit or has_runner_trim or has_level_trim:
        position_note = parse_position_note(cleaned)
        explicit_full_close = re.search(r"\b(ALL OUT|CLOSED|CLOSING|EXIT|EXITED|EXITING|FULL(?:Y)? OUT|BREAKEVEN EXIT|BREAK EVEN EXIT|B/E|AT B/E|AT BE|AT EVEN)\b", upper)
        trim_like_exit = (
            _contains_any(upper, ("TRIM", "TRIMMED", "TRIMMING", "SCALED", "SCALE OUT", "SCALING OUT", "LOCK PROFITS", "LOCKING PROFITS", "PROFIT TAKING"))
            or re.search(r"\bLOCK(?:ING)?\s+SOME\s+PROFITS?\b", upper)
            or re.search(r"\bREDUCE\s+RISK\b", upper)
            or re.search(r"\bTAKE\s+\d{1,3}%\s+OFF\b", upper)
            or re.search(r"\bSCAL(?:E|ING)\s+SOME\s+OUT\b", upper)
            or re.search(r"\b(?:TAK(?:E|ING|EN|E SOME|ING SOME)|TOOK|SELL(?:ING)?|SOLD)\s+(?:SOME|PROFITS?)\b", upper)
            or re.search(r"\bSOME\s+OFF\b", upper)
        )
        action = "trim" if trim_like_exit else "close"
        if position_note and not explicit_full_close:
            action = "trim"
        if has_runner_trim and not has_exit:
            action = "trim"
        if has_level_trim:
            action = "trim"
            price = None
        price = parse_exit_price(cleaned)
        if has_level_trim:
            price = None
    elif structured_stock_entry:
        action = "entry"
    elif has_strong_add:
        if re.search(r"\b(?:AVG|AVERAG(?:E|ED|ING))\s+DOWN\b|\bDOUBLE\s+DOWN\b|\bADDING\s+LOWER\b", upper):
            action = "average_down"
        elif re.search(r"\b(?:AVG|AVERAG(?:E|ED|ING))\s+UP\b|\bADDING\s+HIGHER\b", upper):
            action = "average_up"
        else:
            action = "add"
    elif has_strong_entry:
        action = "entry"
    elif has_entry or has_clean_trade_details:
        action = "entry"
    else:
        return None

    if asset_type in {"stock", "future"} and price is None:
        price = parse_market_price(cleaned, ticker)

    confidence = "high"
    if action in {"trim", "close", "exit", "stop"} and not (ticker or contract_match):
        confidence = "medium"
    if action in {"trim", "close"} and has_strong_entry and (has_exit or has_runner_trim):
        confidence = "medium"
    if action in {"add", "average_down", "average_up"} and (price is None or not (ticker or contract_match or asset_type in {"stock", "future"})):
        confidence = "medium"
    if action == "roll_option" and not (contract and (expiration or price is not None)):
        confidence = "medium"

    # Avoid routing generic chat like "I entered..." when no tradable details were found.
    if action == "entry" and not has_clean_trade_details and not (ticker and (contract or (price is not None and asset_type in {"stock", "future"}))):
        return None
    if action == "entry" and has_forward_entry and not has_strong_entry:
        return None
    if action in {"add", "average_down", "average_up"} and _has_forward_add_context(upper):
        return None

    trade_note = parse_position_note(raw) if action in {"trim", "close"} else parse_trade_note(raw)
    expiration, trade_note = apply_default_expiration_and_note(action, expiration, trade_note, asset_type)

    return ParsedAlert(
        action=action,
        confidence=confidence,
        ticker=ticker,
        contract=contract,
        expiration=expiration,
        price=price,
        raw_text=raw,
        trade_note=trade_note,
        old_contract=roll_details.get("old_contract") if roll_details else None,
        old_expiration=roll_details.get("old_expiration") if roll_details else None,
        roll_cost=roll_details.get("roll_cost") if roll_details else None,
        roll_cost_type=roll_details.get("roll_cost_type") if roll_details else None,
        asset_type=asset_type,
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
