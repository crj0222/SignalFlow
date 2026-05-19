from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Analyst:
    id: int
    guild_id: int
    name: str
    is_active: bool
    discord_user_id: Optional[int] = None


@dataclass(frozen=True)
class ParsedAlert:
    action: str
    confidence: str
    ticker: Optional[str]
    contract: Optional[str]
    expiration: Optional[str]
    price: Optional[float]
    raw_text: str
    trade_note: Optional[str] = None

    @property
    def is_entry(self) -> bool:
        return self.action == "entry"

    @property
    def is_exit(self) -> bool:
        return self.action in {"trim", "close", "exit", "stop"}
