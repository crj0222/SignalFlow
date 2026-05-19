import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Optional

from models import ParsedAlert
from parser import parse_alert, parse_price, parse_trade_note


CLASSIFIER_MODEL = os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-4o-mini")
AI_ENABLED = os.getenv("USE_AI_CLASSIFIER", "true").lower() in {"1", "true", "yes", "on"}
CLASSIFIER_TIMEOUT = float(os.getenv("OPENAI_CLASSIFIER_TIMEOUT_SECONDS", "8"))
log = logging.getLogger("signalflow.classifier")
_client = None


LOCAL_CONFIDENCE_WORDS = (
    "BTO",
    "BUYING",
    "BOUGHT",
    "ENTERING",
    "ENTERED",
    "TAKING",
    "TOOK",
    "GRABBED",
    "FILLED",
    "SELL",
    "SOLD",
    "STC",
    "TRIM",
    "TRIMMING",
    "EXIT",
    "CLOSE",
    "STOPPED",
    "STOP HIT",
    "STOPPED OUT",
)

AI_CANDIDATE_WORDS = (
    "OPTION",
    "ENTRY",
    "ENTER",
    "ADD",
    "STARTER",
    "PAID",
    "AVG",
    "AVERAGE",
    "SCALE",
    "CUT",
    "STOP",
    "TRIM",
    "SELL",
    "CLOSE",
    "LOTTO",
    "SWING",
)

OBVIOUS_CHATTER = {
    "GM",
    "GOOD MORNING",
    "THANKS",
    "THANK YOU",
    "NICE",
    "LOL",
    "OK",
    "YES",
    "NO",
}


SYSTEM_PROMPT = """
Classify a Discord trading alert. Return only JSON:
{"action":"entry|trim|exit|stop|ignore","ticker":null,"contract":null,"expiration":null,"price":null,"trade_note":null,"confidence":"normal|possible"}
trade_note should be "Half Size", "Light", "Lotto", "Swing", "Day Trade", a slash-combo like "Light / Lotto", or null.
entry=taking/filled/bought/opening now. trim=partial scale out. exit=closing/selling. stop=stopped out/stop loss hit/cut at stop. ignore=watchlist/idea/maybe/recap/uncertain.
Extract ticker, option contract like 530C, expiration like 5/24, and price. Do not invent missing details. If trim/exit/stop lacks ticker or contract, confidence="possible".
Price means the option fill/entry/trim/exit price, such as "@ 1.20", "at .95", "paid 1.35", "avg 1.10", "filled 2.40", "Entry: 4.20-4.30", or a decimal right after the contract. For ranges, use the first number.
Important: a terse message with ticker + option contract + price, like "SPX 7385C - 3.5", is an entry unless it says watching/possible/maybe/idea/looking for/not in.
Trade ideas, setups, watchlists, "looking for", "love the contract", "will alert entry", "if/over/under trigger" are ignore unless the message clearly says the analyst entered, bought, took, grabbed, filled, sold, trimmed, exited, or stopped right now.
""".strip()


FORWARD_LOOKING_PHRASES = (
    "DAY TRADE IDEA",
    "TRADE IDEA",
    "SETUP",
    "LOOKING FOR",
    "WILL ALERT",
    "WILL ALERT ANY ENTRY",
    "LOVE THE",
    "SHOULD BE INTERESTING",
)

ENTRY_NOW_PHRASES = (
    "BTO",
    "BUYING",
    "BOUGHT",
    "ENTERING",
    "ENTERED",
    "TAKING",
    "TOOK",
    "GRABBED",
    "FILLED",
)


def _looks_forward_only(content: str, parsed: Optional[ParsedAlert]) -> bool:
    if not parsed or parsed.action != "entry":
        return False

    upper = content.upper()
    has_forward_context = any(_has_phrase(upper, phrase) for phrase in FORWARD_LOOKING_PHRASES)
    has_clear_now_action = any(_has_phrase(upper, phrase) for phrase in ENTRY_NOW_PHRASES)
    return has_forward_context and not has_clear_now_action


def _invalid_entry(parsed: Optional[ParsedAlert]) -> bool:
    if not parsed or parsed.action != "entry":
        return False
    return not (parsed.ticker and (parsed.contract or parsed.price is not None))


def _has_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"(?<![A-Z0-9]){re.escape(phrase)}(?![A-Z0-9])", text))


def _sanitize(parsed: Optional[ParsedAlert], content: str) -> Optional[ParsedAlert]:
    return None if (_looks_forward_only(content, parsed) or _invalid_entry(parsed)) else parsed


def _should_skip_ai(content: str) -> bool:
    stripped = content.strip()
    if not stripped:
        return True
    upper = stripped.upper()
    if upper in OBVIOUS_CHATTER:
        return True
    if len(stripped) > 500 and not re.search(r"\b\d{1,5}\s*[CP]\b", upper):
        return True
    has_ticker = bool(re.search(r"\b[A-Z]{1,5}\b", upper))
    has_contract = bool(re.search(r"\b\d{1,5}(?:\.\d{1,2})?\s*[CP]\b", upper))
    has_decimal = bool(re.search(r"(?<![\d/])(?:\d+)?\.\d{1,2}(?![\d/])", upper))
    has_actionish = any(_has_phrase(upper, word) for word in AI_CANDIDATE_WORDS)
    return not (has_contract or (has_ticker and has_decimal) or has_actionish)


def _local_is_confident(content: str, parsed: ParsedAlert) -> bool:
    upper = content.upper()
    if parsed.action == "entry":
        has_terse_full_details = bool(parsed.ticker and parsed.contract and parsed.price is not None)
        has_clear_entry_word = any(_has_phrase(upper, word) for word in LOCAL_CONFIDENCE_WORDS)
        return has_terse_full_details or has_clear_entry_word
    return bool(parsed.ticker or parsed.contract)


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        _client = AsyncOpenAI(max_retries=0, timeout=CLASSIFIER_TIMEOUT)
    return _client


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(",", "")
        range_match = re.match(r"((?:\d+)?\.\d{1,2}|\d+)", value)
        if range_match:
            value = range_match.group(1)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def today_expiration() -> str:
    now = datetime.now()
    return f"{now.month}/{now.day}"


def _parsed_from_payload(content: str, payload: dict[str, Any]) -> Optional[ParsedAlert]:
    action = str(payload.get("action", "ignore")).lower().strip()
    if action == "ignore":
        return None
    if action not in {"entry", "trim", "exit", "stop"}:
        return None

    confidence = str(payload.get("confidence", "normal")).lower().strip()
    if confidence not in {"normal", "possible"}:
        confidence = "normal"

    expiration = str(payload["expiration"]).strip() if payload.get("expiration") else today_expiration()

    contract = str(payload["contract"]).upper().strip() if payload.get("contract") else None
    price = _coerce_float(payload.get("price"))
    trade_note = str(payload["trade_note"]).strip() if payload.get("trade_note") else parse_trade_note(content)

    return ParsedAlert(
        action=action,
        confidence=confidence,
        ticker=str(payload["ticker"]).upper().strip() if payload.get("ticker") else None,
        contract=contract,
        expiration=expiration,
        price=price if price is not None else parse_price(content, contract),
        raw_text=content.strip(),
        trade_note=trade_note,
    )


async def _classify_with_openai(content: str) -> Optional[ParsedAlert]:
    client = _get_client()
    current_expiration = today_expiration()
    response = await client.chat.completions.create(
        model=CLASSIFIER_MODEL,
        response_format={"type": "json_object"},
        temperature=0,
        max_completion_tokens=120,
        messages=[
            {
                "role": "system",
                "content": f"{SYSTEM_PROMPT}\nIf no expiration is explicitly listed, use today's date: {current_expiration}.",
            },
            {"role": "user", "content": content},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    return _parsed_from_payload(content, payload)


async def classify_alert(content: str) -> Optional[ParsedAlert]:
    """Prefer cheap local checks; use AI only for ambiguous alert-shaped messages."""
    local_parsed = _sanitize(parse_alert(content), content)
    if local_parsed and _local_is_confident(content, local_parsed):
        return local_parsed

    if _should_skip_ai(content):
        return local_parsed

    if AI_ENABLED and os.getenv("OPENAI_API_KEY"):
        try:
            parsed = await _classify_with_openai(content)
            return _sanitize(parsed, content)
        except Exception as exc:
            # Keep the bot running if the AI provider is unavailable.
            log.warning("AI classification unavailable; using local parser fallback. Reason: %s", exc)
            return local_parsed

    return local_parsed
