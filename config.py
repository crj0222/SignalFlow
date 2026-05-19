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
    clear_guild_commands: bool
    owner_ids: set[int]


def _clean_env_value(value: str) -> str:
    cleaned = value.strip()
    if "=" in cleaned and cleaned.split("=", 1)[0].strip().isupper():
        cleaned = cleaned.split("=", 1)[1].strip()
    return cleaned.strip().strip('"').strip("'").strip()


def load_config() -> BotConfig:
    token = _clean_env_value(os.getenv("DISCORD_BOT_TOKEN", ""))
    guild_id_raw = _clean_env_value(os.getenv("GUILD_ID", ""))
    database_path = _clean_env_value(os.getenv("DATABASE_PATH", "signalflow.sqlite3"))
    clear_guild_commands = _clean_env_value(os.getenv("CLEAR_GUILD_COMMANDS", "")).lower() in {"1", "true", "yes", "on"}
    owner_ids_raw = _clean_env_value(os.getenv("OWNER_IDS", ""))

    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing. Add it to your .env file.")

    guild_id = int(guild_id_raw) if guild_id_raw else None
    owner_ids = {int(value.strip()) for value in owner_ids_raw.split(",") if value.strip().isdigit()}
    return BotConfig(
        token=token,
        guild_id=guild_id,
        database_path=database_path,
        clear_guild_commands=clear_guild_commands,
        owner_ids=owner_ids,
    )
