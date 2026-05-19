import csv
import io
import re
from collections import OrderedDict

VALID_EXAMPLE_ACTIONS = (
    "entry",
    "add",
    "average_down",
    "average_up",
    "trim",
    "close",
    "roll_option",
    "ignore",
)

IGNORE_PATTERNS = (
    r"\b(past\s+\d+\s+weeks?\s+recap|recap|best of|results)\b",
    r"^\s*ticker\s*:",
    r"\b(day trade|lotto|swing)\s+idea\b",
    r"\b(watchlist|watching|watching for|on watch|on radar|radar)\b",
    r"\b(looking for|looking to|waiting for|will alert|stay posted|love the)\b",
    r"\b(possible|potential|maybe|setup|planned plays?)\b",
    r"\b(good morning|morning fam|market outlook)\b",
)

SKIP_PATTERNS = (
    r"^@?[a-z0-9_. ]+\s+your bot\b",
    r"\bserver registered successfully\b",
    r"^\s*@(?:everyone|here)\s*$",
    r"^\s*(test|bounded)\s*$",
)
ENTRY_PATTERNS = (
    r"(?:^|[\s|])(?:BTO|STO|OPEN|ENTER(?:ING|ED)?|I'?M ENTERING|GRABB(?:ED|ING)?|TAKING|TOOK|FILLED|BUYING|BOUGHT)\b",
    r"\b[A-Z]{1,5}\s+HERE\b.*\b\d{1,2}/\d{1,2}\s+\d{1,5}(?:\.\d{1,2})?\s*[CP]\b.*\bAVG\b",
    r"^\s*\$?[A-Z]{1,5}\s+\d{1,5}(?:\.\d{1,2})?\s*[CP]\s*[-@]\s*(?:\d+)?\.\d{1,2}\b",
)
TRIM_PATTERNS = (
    r"\b(TRIM|TRIMMED|TRIMMING|TAKE A TRIM|TAKE PROFITS?|TAKING PROFITS?|LOCK PROFITS?|SECURE PROFITS?)\b",
    r"\b(?:DOWN TO|REDUCED TO|CUT TO)\s+(?:\d+/\d+|\d{1,3}%|RUNNERS?|A RUNNER)\b",
    r"\+\d{1,4}(?:\.\d+)?%\s+(?:HERE\s+)?ON\s+\$?[A-Z]{1,5}\b",
)
EXIT_PATTERNS = (
    r"\b(CLOSED|CLOSE HERE|YOU CAN CLOSE HERE|CLOSE REMAINING|CLOSE RUNNERS?|ALL OUT|SOLD EVERYTHING|OUT OF (?:MY )?REMAINING)\b",
)
STOP_PATTERNS = (
    r"^\s*(?:\*\*)?(?:\$?[A-Z]{1,5}\s+)?STOPPED OUT\b",
    r"\bSTOPPED OUT OF (?:MY |THE |OUR |REMAINING )?\$?[A-Z]{1,5}\b",
    r"\b(?:STOP HIT|STOP LOSS HIT|SL HIT|CUT HERE)\b",
)


def _clean_text(value: str) -> str:
    return " ".join((value or "").replace("\r", "\n").split()).strip()


def _looks_like_ignore_example(text: str) -> bool:
    lowered = text.lower()
    has_trade_shape = bool(
        re.search(r"\b[A-Z]{1,5}\b", text)
        or re.search(r"\b\d{1,5}(?:\.\d{1,2})?\s*(?:c|p|call|put)\b", text, re.IGNORECASE)
    )
    return has_trade_shape and any(re.search(pattern, lowered) for pattern in IGNORE_PATTERNS)


def _has_trade_shape(text: str) -> bool:
    return bool(
        re.search(r"\$?[A-Z]{1,5}\b", text)
        and (
            re.search(r"\b\d{1,5}(?:\.\d{1,2})?\s*(?:C|P|CALL|PUT)\b", text, re.IGNORECASE)
            or re.search(r"@\s*(?:\d+)?\.\d{1,2}\b", text)
            or re.search(r"(?<![\d/])(?:\d+)?\.\d{1,2}\s*[-\u2013\u2014]\s*(?:\d+)?\.\d{1,2}(?![\d/])", text)
            or re.search(r"[+-]\d{1,4}(?:\.\d+)?%", text)
        )
    )


def _match_any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _classify_csv_example(text: str) -> str | None:
    upper = text.upper()
    if _match_any(ENTRY_PATTERNS, upper) and _has_trade_shape(text):
        return "entry"
    if _match_any(STOP_PATTERNS, upper):
        if re.search(r"\b(IF|UNLESS|WOULD|WILL)\b.{0,80}\bSTOPPED OUT\b", upper):
            return "ignore" if _looks_like_ignore_example(text) else None
        if re.search(r"\bCUT\s+(?:MY|THE|THIS|OUR)?\s*POSITION\s+IN\s+HALF\b", upper):
            return "trim" if _has_trade_shape(text) else None
        return "close"
    if _match_any(TRIM_PATTERNS, upper) and _has_trade_shape(text):
        return "trim"
    if _match_any(EXIT_PATTERNS, upper) and _has_trade_shape(text):
        return "close"
    if _looks_like_ignore_example(text):
        return "ignore"
    return None


def _should_skip(text: str) -> bool:
    lowered = text.lower()
    return len(text) < 8 or any(re.search(pattern, lowered) for pattern in SKIP_PATTERNS)


def _content_column(fieldnames: list[str] | None) -> str | None:
    if not fieldnames:
        return None
    for name in fieldnames:
        if name.lower().strip() in {"content", "text", "example_text", "message"}:
            return name
    return None


def _add_example(examples: dict[str, OrderedDict[str, None]], action: str, text: str, per_action_limit: int) -> None:
    if action not in examples or len(examples[action]) >= per_action_limit:
        return
    examples[action].setdefault(text, None)


def examples_from_csv_bytes(
    data: bytes,
    per_action_limit: int = 30,
    fixed_action: str | None = None,
) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Extract classifier examples from a Discord or curated CSV export."""
    per_action_limit = max(1, min(per_action_limit, 500))
    if fixed_action and fixed_action not in VALID_EXAMPLE_ACTIONS:
        raise ValueError("Invalid example action.")

    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    content_column = _content_column(reader.fieldnames)
    if not content_column:
        raise ValueError("CSV must include a Content, text, example_text, or message column.")

    examples: dict[str, OrderedDict[str, None]] = {action: OrderedDict() for action in VALID_EXAMPLE_ACTIONS}
    stats = {"rows": 0, "blank": 0, "skipped": 0, "saved": 0}

    for row in reader:
        stats["rows"] += 1
        clean = _clean_text(row.get(content_column, ""))
        if not clean:
            stats["blank"] += 1
            continue
        if _should_skip(clean):
            stats["skipped"] += 1
            continue

        action = fixed_action or _classify_csv_example(clean)
        if action:
            _add_example(examples, action, clean, per_action_limit)
            continue

        stats["skipped"] += 1

    result = {action: list(values.keys()) for action, values in examples.items()}
    stats["saved"] = sum(len(values) for values in result.values())
    for action, values in result.items():
        stats[action] = len(values)
    return result, stats
