import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class BotConfig:
    token: str
    guild_id: Optional[int]
    database_path: str


def load_config() -> BotConfig:
    token = os.getenv("DISCORD_BOT_TOKEN", "").strip()
    guild_id_raw = os.getenv("GUILD_ID", "").strip()
    database_path = os.getenv("DATABASE_PATH", "signalflow.sqlite3").strip()

    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing. Add it to your .env file.")

    guild_id = int(guild_id_raw) if guild_id_raw else None
    return BotConfig(token=token, guild_id=guild_id, database_path=database_path)
