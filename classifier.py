import json
import logging
import os
import re
from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable, Optional

from models import ParsedAlert
from parser import (
    COMMON_NON_TICKERS,
    apply_default_expiration_and_note,
    current_week_friday_expiration,
    normalize_contract,
    parse_alert,
    parse_position_note,
    parse_price,
    parse_roll_cost,
    parse_trade_note,
)


CLASSIFIER_MODEL = os.getenv("OPENAI_CLASSIFIER_MODEL", "gpt-4o-mini")
AI_ENABLED = os.getenv("USE_AI_CLASSIFIER", "true").lower() in {"1", "true", "yes", "on"}
IMAGE_AI_ENABLED = os.getenv("USE_IMAGE_CLASSIFIER", "true").lower() in {"1", "true", "yes", "on"}
CLASSIFIER_TIMEOUT = float(os.getenv("OPENAI_CLASSIFIER_TIMEOUT_SECONDS", "8"))
log = logging.getLogger("signalflow.classifier")
_client = None
MAX_EXAMPLES_PER_ACTION = 6
CLASSIFIER_ACTIONS = ("entry", "add", "average_down", "average_up", "trim", "close", "roll_option", "ignore")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")


def _is_gpt5_model(model: str) -> bool:
    return model.lower().startswith("gpt-5")


def _completion_token_limit(model: str) -> int:
    # GPT-5 reasoning models may spend part of this budget on reasoning before JSON output.
    return 500 if _is_gpt5_model(model) else 180


LOCAL_CONFIDENCE_WORDS = (
    "B/E",
    "BREAKEVEN",
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
    "ADD",
    "ADDED",
    "ADDING",
    "AVG",
    "AVERAGE",
    "AVERAGED",
    "AVERAGING",
    "B/E",
    "AT BE",
    "BREAKEVEN",
    "BTO",
    "BUY",
    "BOUGHT",
    "CLOSE",
    "CUT",
    "ENTER",
    "ENTERED",
    "ENTERING",
    "ENTRY",
    "EXIT",
    "FILLED",
    "GRABBED",
    "LONG",
    "OPEN",
    "OPENING",
    "OPTION",
    "OUT",
    "PAID",
    "POSITION",
    "ROLL",
    "ROLLED",
    "ROLLING",
    "SCALE",
    "SELL",
    "SOLD",
    "STARTER",
    "STC",
    "STOP",
    "STOPPED",
    "STO",
    "TAKING",
    "TOOK",
    "TRIM",
    "TRIMMED",
    "LOTTO",
    "PROFIT",
    "PROFITS",
    "REDUCED RISK",
    "RUNNER",
    "RUNNERS",
    "SWING",
    "LOCK",
    "LOCKING",
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
{"action":"entry|add|average_down|average_up|trim|close|roll_option|ignore","ticker":null,"contract":null,"expiration":null,"price":null,"old_contract":null,"old_expiration":null,"roll_cost":null,"roll_cost_type":null,"trade_note":null,"visible_text":null,"confidence":"high|medium|low"}
For entry/add/average alerts, trade_note should be "Half Size", "Light", "Lotto", "Swing", "0DTE", "Day Trade", a slash-combo like "Swing / Half Size", or null. Only use "Day Trade" if the message explicitly says day trade/daytrade/scalp/intraday. For trim alerts, trade_note should be one clean current-position label such as "Half Position", "1/3 Position", "1/4 Position", "Runners", "Risk Free", "Majority Trimmed", or null. Do not mix entry sizing notes like Light/Half Size into trim trade_note.
entry=taking/filled/bought/opening/grabbing/starting a new position now. add=adding/reloading/averaging into an existing position when you cannot know from text whether the add is up or down. average_down=explicit averaging down. average_up=explicit averaging up. trim=partial scale out/trim/sell some while keeping runners. close=fully closed/all out/sold/STC/stopped out/cut/breakeven/B/E/at even exit/invalidated. roll_option=rolling from one option contract to another. ignore=watchlist/idea/maybe/recap/uncertain/chat.
Extract ticker, option contract like 530C, expiration like 5/24, and price. Do not invent missing details. If trim/close/add/roll lacks ticker or contract, use confidence="medium" unless the wording clearly points to the analyst's most recent position. Short messages like "added to SPY @.7" are add alerts and should use the latest open SPY position. Short messages like "exiting trade at B/E" are close alerts for the latest open position.
If the message says weeklies/weekly/wkly, expiration means the Friday of the current week.
Lines like "Ticker: QQQ" explicitly identify the ticker. Possessive labels like "Expo's Trim" are analyst/template labels, not tickers.
Use confidence high only when this is clearly an executable alert/update now. Use medium for ambiguous but possibly actionable messages that need review. Use low for weak/uncertain messages. Prioritize avoiding false alerts.
Price means the option fill/entry/trim/close price, such as "@ 1.20", "at .95", "paid 1.35", "avg 1.10", "filled 2.40", "Entry: 4.20-4.30", or a decimal right after the contract. For entry ranges, use the first number. For trim/close ranges like "1.50 - 1.90", use the second/current price.
Trim ladder lines like "3.1 - trim" or "3.5 - 50%" are trim alerts for the analyst's most recent position. In "3.5 - 50%", 3.5 is the trim price and 50% is position size, not profit. Only treat a percent as gain/loss when it has a sign like "+50%" or wording like gain/profit/loss/down.
For screenshots/images, put the important OCR text you can read in visible_text, especially ticker, expiration/contract text, and percentages. Image cards like "SPX (SPXW) May19..." with a green "184.5%" are trim/update alerts for the most recent matching SPX position; set action="trim", ticker="SPX", price=null, visible_text with "SPX ... 184.5%", and confidence="medium" unless the caption clearly says trim/close.
If a message starts with a style label like "Day Trade:", "Lotto:", "Swing:", or "Light:", that label is not the ticker. The ticker is the symbol next to the contract, e.g. "Day Trade: SPY 770c May 29 @.36" has ticker SPY.
Role pings and author tags like "@Waxui Alerts", "@Are Ping", and "@Scott Alerts" are not tickers.
Important: a terse message with ticker + option contract + price, like "SPX 7385C - 3.5", is an entry unless it says watching/possible/maybe/idea/looking for/not in.
Trade ideas, setups, watchlists, "looking for", "watching", "eyeing", "on radar", "potential", "possible", "waiting for", "love the contract", "will alert entry", "if/over/under trigger" are ignore unless the message clearly says the analyst entered, bought, took, grabbed, filled, sold, trimmed, closed, or stopped right now.
Common current-entry words include BTO, STO, bought, buying, entered, entering, taking, took, grabbed, filled, opening, starter, long. Common add words include add, added, adding, added to, reload, reloaded, averaging, average down, average up, back in. Common trim words include trim, trimmed, scale out, reduced risk, down to runners, down to 1/3 position, take a trim, take profits. Common close words include closed, sold, STC, all out, exited, exiting, stopped out, stop hit, cut here, breakeven, B/E, at even, invalidated. Common roll words include roll, rolling, rolled, roll these back/out/forward.
Formats seen in real analyst feeds:
- "@Waxui Alerts *High Risk* | SPY here | 03/10 677P | Avg, 2.25" is an entry.
- "OPEN $NAVN $20 call 6/18 @ 1.80 (swing, half sized for now)" is an entry.
- "Adding NVDA 225C @ 2.10" is add unless it explicitly says average down/up.
- "added to SPY @.7" is add for the latest open SPY position.
- "Averaging down SPY 530C at .80" is average_down.
- "Adding higher on runners, SPY 530C @ 1.50" is add.
- "Trim SPY here | 1.50 - 1.90 | 27%" is a trim; use 1.90 as the trim price.
- "3.1 - trim\n3.5 - 50%" is a trim with price=3.5 and trade_note="Half Position"; ticker/contract can be null with medium confidence because it refers to the most recent position.
- Image/screenshot showing "SPX (SPXW) May19..." and "184.5%" is a trim/update; ticker=SPX, price=null, visible_text should include "184.5%" so gain math can use it.
- "Closed SPY here" is close.
- "Stopped out of SPX" is close.
- "Cutting here at breakeven" is close.
- "exiting trade at B/E" is close for the latest open position.
- "+20% here on $SOFI calls, take a trim" is a trim.
- "down to 1/3 position MSFT @1.4" is a trim, not an entry.
- "Rolling SPY 5/24 530C to 5/31 540C for .25 debit" is roll_option; old_contract=530C, old_expiration=5/24, contract=540C, expiration=5/31, roll_cost=.25, roll_cost_type=debit.
- "$GOOGL roll these back to BTO 1/16 $320c @ 4.65" is roll_option with contract=320C, expiration=1/16, price=4.65.
- "Looking to enter near open ... BTO 4/2 $200c" is ignore if no actual fill/entry price is given.
""".strip()


FORWARD_LOOKING_PHRASES = (
    "DAY TRADE IDEA",
    "LOOKING TO ENTER",
    "MAY CONSIDER",
    "NEAR OPEN",
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
    if _looks_forward_only(content, parsed) or _invalid_entry(parsed):
        return None
    if not parsed:
        return None

    stripped = content.strip()
    upper = stripped.upper()
    if stripped.endswith("?") and len(stripped) <= 40:
        return replace(parsed, confidence="medium")

    has_entry_now = any(_has_phrase(upper, phrase) for phrase in ENTRY_NOW_PHRASES)
    has_exit_now = any(
        _has_phrase(upper, phrase)
        for phrase in (
            "TRIM",
            "TRIMMED",
            "TAKING A TRIM",
            "TAKE PROFITS",
            "TAKING PROFITS",
            "SOLD",
            "SELL",
            "STC",
            "CLOSE",
            "CLOSED",
            "EXIT",
            "STOPPED OUT",
            "STOP HIT",
            "CUT",
        )
    )
    if has_entry_now and has_exit_now:
        return replace(parsed, confidence="medium")

    if parsed.action in {"add", "average_down", "average_up"} and parsed.price and parsed.price > 100 and not parsed.contract:
        return replace(parsed, confidence="medium")

    return parsed


def _prefer_local_exit_action(parsed: Optional[ParsedAlert], content: str) -> Optional[ParsedAlert]:
    local = parse_alert(content)
    if not parsed:
        if local and local.action in {"add", "average_down", "average_up", "trim", "close", "roll_option"}:
            return local
        return None

    if not parsed:
        return parsed

    if (
        parsed.action in {"add", "average_down", "average_up"}
        and local
        and local.action == "entry"
        and re.search(r"\b(?:BTO|BUY(?:ING)?|BOUGHT|ENTER(?:ING|ED)?|OPEN(?:ING)?|GRABB(?:ED|ING)?|FILL(?:ED)?)\b", content, re.IGNORECASE)
    ):
        return local
    if parsed.action == "close" and local and local.action == "trim":
        return local

    actionable_local = local and local.action in {"add", "average_down", "average_up", "trim", "close", "roll_option"}
    if not actionable_local:
        return parsed

    should_prefer_local = (
        parsed.action == "entry"
        or parsed.confidence != "high"
        or (not parsed.ticker and local.ticker)
        or (parsed.price is None and local.price is not None)
        or (parsed.action == local.action and parsed.contract is None and local.contract is not None)
    )
    if not should_prefer_local:
        return parsed

    return replace(
        parsed,
        action=local.action,
        confidence="high" if local.confidence == "high" else parsed.confidence,
        ticker=local.ticker or parsed.ticker,
        contract=local.contract or parsed.contract,
        expiration=local.expiration or parsed.expiration,
        price=local.price if local.price is not None else parsed.price,
        trade_note=local.trade_note or parsed.trade_note,
        old_contract=local.old_contract or parsed.old_contract,
        old_expiration=local.old_expiration or parsed.old_expiration,
        roll_cost=parsed.roll_cost if parsed.roll_cost is not None else local.roll_cost,
        roll_cost_type=parsed.roll_cost_type or local.roll_cost_type,
    )


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
    has_contract = bool(re.search(r"\b\d{1,5}(?:\.\d{1,2})?\s*(?:[CP]|CALLS?|PUTS?)\b", upper))
    has_decimal = bool(re.search(r"(?<![\d/])(?:\d+)?\.\d{1,2}(?![\d/])", upper))
    has_actionish = any(_has_phrase(upper, word) for word in AI_CANDIDATE_WORDS)
    return not (has_contract or (has_ticker and has_decimal) or has_actionish)


def _get_client():
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        _client = AsyncOpenAI(api_key=api_key, max_retries=0, timeout=CLASSIFIER_TIMEOUT)
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


def _coerce_ticker(value: Any) -> Optional[str]:
    ticker = str(value or "").upper().strip().lstrip("$")
    if not ticker or ticker.lower() in {"null", "none", "n/a", "na"}:
        return None
    return None if ticker in COMMON_NON_TICKERS else ticker


def today_expiration() -> str:
    now = datetime.now()
    return f"{now.month}/{now.day}"


def _example_value(example: Any, key: str) -> Any:
    try:
        return example[key]
    except (KeyError, TypeError):
        return getattr(example, key, None)


def _format_examples(examples: Optional[Iterable[Any]]) -> str:
    if not examples:
        return ""

    grouped: dict[str, list[str]] = {action: [] for action in CLASSIFIER_ACTIONS}
    for example in examples:
        action = str(_example_value(example, "action") or "").lower().strip()
        if action in {"exit", "stop"}:
            action = "close"
        text = str(_example_value(example, "example_text") or "").strip()
        if action not in grouped or not text:
            continue
        if len(grouped[action]) >= MAX_EXAMPLES_PER_ACTION:
            continue
        text = " ".join(text.split())
        if len(text) > 260:
            text = f"{text[:257]}..."
        grouped[action].append(text)

    lines = []
    for action, texts in grouped.items():
        for text in texts:
            lines.append(f'- "{text}" -> {action}')
    if not lines:
        return ""

    return (
        "\n\nServer-specific examples. Treat these as the strongest guide for this server's wording:\n"
        + "\n".join(lines)
    )


def _parsed_from_payload(content: str, payload: dict[str, Any]) -> Optional[ParsedAlert]:
    action = str(payload.get("action", "ignore")).lower().strip()
    if action == "ignore":
        return None
    if action in {"exit", "stop"}:
        action = "close"
    if action in {"roll", "rolled", "rolling"}:
        action = "roll_option"
    if action not in set(CLASSIFIER_ACTIONS):
        return None

    confidence = str(payload.get("confidence", "high")).lower().strip()
    confidence = {"normal": "high", "possible": "medium"}.get(confidence, confidence)
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    expiration = str(payload["expiration"]).strip() if payload.get("expiration") else None
    if re.search(r"\b(WEEKLIES|WEEKLY|WKLY|WEEKLYS)\b", content, flags=re.IGNORECASE):
        expiration = current_week_friday_expiration()

    contract = normalize_contract(str(payload["contract"]).upper().strip()) if payload.get("contract") else None
    old_contract = normalize_contract(str(payload["old_contract"]).upper().strip()) if payload.get("old_contract") else None
    price = _coerce_float(payload.get("price"))
    roll_cost = _coerce_float(payload.get("roll_cost"))
    roll_cost_type = str(payload.get("roll_cost_type") or "").lower().strip() or None
    if roll_cost_type not in {None, "debit", "credit"}:
        roll_cost_type = None
    if action == "roll_option" and roll_cost is None:
        roll_cost, roll_cost_type = parse_roll_cost(content)
    if action == "roll_option" and roll_cost_type and price == roll_cost:
        price = None
    if action in {"trim", "close"}:
        trade_note = _coerce_position_note(payload.get("trade_note"), content)
    else:
        trade_note = _coerce_trade_note(payload.get("trade_note"), content)
    expiration, trade_note = apply_default_expiration_and_note(action, expiration, trade_note)

    visible_text = str(payload.get("visible_text") or "").strip()
    raw_text = "\n".join(part for part in [content.strip(), visible_text] if part)

    return ParsedAlert(
        action=action,
        confidence=confidence,
        ticker=_coerce_ticker(payload.get("ticker")),
        contract=contract,
        expiration=expiration,
        price=price if price is not None else parse_price(content, contract),
        raw_text=raw_text,
        trade_note=trade_note,
        old_contract=old_contract,
        old_expiration=str(payload["old_expiration"]).strip() if payload.get("old_expiration") else None,
        roll_cost=roll_cost,
        roll_cost_type=roll_cost_type,
    )


def _coerce_trade_note(value: Any, content: str) -> str:
    local_note = parse_trade_note(content)
    if value in (None, ""):
        return local_note
    note = str(value).strip()
    if not note or note.lower() in {"null", "none", "n/a", "na"}:
        return local_note

    merged = []
    for part in [*local_note.split(" / "), *note.split(" / ")]:
        clean = part.strip()
        if clean and clean not in merged:
            merged.append(clean)
    return " / ".join(merged)


def _merge_notes(*notes: Optional[str]) -> str:
    merged = []
    for note in notes:
        for part in (note or "").split(" / "):
            clean = part.strip()
            if clean and clean not in merged:
                merged.append(clean)
    return " / ".join(merged)


def _coerce_position_note(value: Any, content: str) -> str:
    local_note = parse_position_note(content)
    if local_note:
        return local_note

    allowed = {
        "half position": "Half Position",
        "1/2 position": "Half Position",
        "one half position": "Half Position",
        "1/3 position": "1/3 Position",
        "one third position": "1/3 Position",
        "1/4 position": "1/4 Position",
        "one quarter position": "1/4 Position",
        "runners": "Runners",
        "runner": "Runners",
        "risk free": "Risk Free",
        "risk-free": "Risk Free",
        "majority trimmed": "Majority Trimmed",
    }
    note = str(value or "").strip().lower()
    for part in note.split("/"):
        clean = part.strip()
        if clean in allowed:
            return allowed[clean]
    return ""


async def _classify_with_openai(content: str, examples: Optional[Iterable[Any]] = None) -> Optional[ParsedAlert]:
    client = _get_client()
    current_expiration = today_expiration()
    examples_prompt = _format_examples(examples)
    request: dict[str, Any] = {
        "model": CLASSIFIER_MODEL,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": _completion_token_limit(CLASSIFIER_MODEL),
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{SYSTEM_PROMPT}{examples_prompt}\n"
                    f"If no expiration is explicitly listed for an entry/add/average alert, use today's date: {current_expiration}, "
                    "and include Day Trade in trade_note unless another explicit duration is present."
                ),
            },
            {"role": "user", "content": content},
        ],
    }
    if not _is_gpt5_model(CLASSIFIER_MODEL):
        request["temperature"] = 0

    response = await client.chat.completions.create(**request)
    payload = json.loads(response.choices[0].message.content or "{}")
    return _parsed_from_payload(content, payload)


async def _classify_image_with_openai(
    image_url: str,
    caption: str = "",
    examples: Optional[Iterable[Any]] = None,
) -> Optional[ParsedAlert]:
    client = _get_client()
    current_expiration = today_expiration()
    examples_prompt = _format_examples(examples)
    text = caption.strip() or "No message text. Classify the trading alert visible in this screenshot/image."
    request: dict[str, Any] = {
        "model": CLASSIFIER_MODEL,
        "response_format": {"type": "json_object"},
        "max_completion_tokens": _completion_token_limit(CLASSIFIER_MODEL),
        "messages": [
            {
                "role": "system",
                "content": (
                    f"{SYSTEM_PROMPT}{examples_prompt}\n"
                    "You may receive a screenshot/image from Discord. Read the visible text in the image and classify it. "
                    "If the image is a trim/close/update card with ticker and price, classify it as the matching alert. "
                    "Ignore chart-only screenshots or images without clear trading-alert text.\n"
                    f"If no expiration is explicitly listed for an entry/add/average alert, use today's date: {current_expiration}, "
                    "and include Day Trade in trade_note unless another explicit duration is present."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    }
    if not _is_gpt5_model(CLASSIFIER_MODEL):
        request["temperature"] = 0

    response = await client.chat.completions.create(**request)
    payload = json.loads(response.choices[0].message.content or "{}")
    return _parsed_from_payload(caption or "[image alert]", payload)


async def classify_alert(content: str, examples: Optional[Iterable[Any]] = None) -> Optional[ParsedAlert]:
    """Use AI for alert-shaped messages; keep local parser as fallback."""
    if _should_skip_ai(content):
        return _sanitize(parse_alert(content), content)

    if AI_ENABLED and os.getenv("OPENAI_API_KEY", "").strip():
        try:
            parsed = await _classify_with_openai(content, examples)
            return _sanitize(_prefer_local_exit_action(parsed, content), content)
        except Exception as exc:
            # Keep the bot running if the AI provider is unavailable.
            log.warning(
                "AI classification unavailable; using local parser fallback. Error=%s Reason=%r",
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            return _sanitize(parse_alert(content), content)

    return _sanitize(parse_alert(content), content)


async def classify_image_alert(
    image_url: str,
    caption: str = "",
    examples: Optional[Iterable[Any]] = None,
) -> Optional[ParsedAlert]:
    """Classify a Discord image attachment when the alert text is inside the image."""
    if not IMAGE_AI_ENABLED or not AI_ENABLED or not os.getenv("OPENAI_API_KEY", "").strip():
        return None

    try:
        parsed = await _classify_image_with_openai(image_url, caption, examples)
        return _sanitize(_prefer_local_exit_action(parsed, caption), caption or "[image alert]")
    except Exception as exc:
        log.warning(
            "Image AI classification unavailable. Error=%s Reason=%r",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        return None
