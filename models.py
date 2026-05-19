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
    old_contract: Optional[str] = None
    old_expiration: Optional[str] = None
    old_price: Optional[float] = None
    roll_cost: Optional[float] = None
    roll_cost_type: Optional[str] = None

    @property
    def is_entry(self) -> bool:
        return self.action == "entry"

    @property
    def is_position_add(self) -> bool:
        return self.action in {"add", "average_down", "average_up"}

    @property
    def is_roll(self) -> bool:
        return self.action == "roll_option"

    @property
    def is_exit(self) -> bool:
        return self.action in {"trim", "close", "exit", "stop"}
