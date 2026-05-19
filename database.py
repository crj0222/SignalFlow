import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from models import Analyst, ParsedAlert


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True) if Path(path).parent != Path(".") else None
        self.init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS guilds (
                    guild_id INTEGER PRIMARY KEY,
                    review_channel_id INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    disabled_reason TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS analysts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    discord_user_id INTEGER,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(guild_id, name COLLATE NOCASE),
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS analyst_channels (
                    guild_id INTEGER NOT NULL,
                    analyst_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY(guild_id, analyst_id),
                    UNIQUE(guild_id, channel_id),
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                    FOREIGN KEY(analyst_id) REFERENCES analysts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    analyst_id INTEGER NOT NULL,
                    is_paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(guild_id, user_id, analyst_id),
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                    FOREIGN KEY(analyst_id) REFERENCES analysts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    analyst_id INTEGER NOT NULL,
                    entry_alert_id INTEGER,
                    current_alert_id INTEGER,
                    ticker TEXT,
                    contract TEXT,
                    expiration TEXT,
                    entry_price REAL,
                    average_price REAL,
                    add_count INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'open',
                    opened_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    closed_at TEXT,
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                    FOREIGN KEY(analyst_id) REFERENCES analysts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS alert_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    analyst_id INTEGER NOT NULL,
                    channel_id INTEGER,
                    message_id INTEGER,
                    action TEXT NOT NULL,
                    confidence TEXT NOT NULL DEFAULT 'normal',
                    ticker TEXT,
                    contract TEXT,
                    expiration TEXT,
                    price REAL,
                    old_contract TEXT,
                    old_expiration TEXT,
                    old_price REAL,
                    roll_cost REAL,
                    roll_cost_type TEXT,
                    parent_alert_id INTEGER,
                    trade_note TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    raw_text TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                    FOREIGN KEY(analyst_id) REFERENCES analysts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS position_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    analyst_id INTEGER NOT NULL,
                    position_id INTEGER NOT NULL,
                    alert_id INTEGER,
                    event_type TEXT NOT NULL,
                    ticker TEXT,
                    old_contract TEXT,
                    old_expiration TEXT,
                    old_price REAL,
                    new_contract TEXT,
                    new_expiration TEXT,
                    new_price REAL,
                    roll_cost REAL,
                    roll_cost_type TEXT,
                    raw_text TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                    FOREIGN KEY(analyst_id) REFERENCES analysts(id) ON DELETE CASCADE,
                    FOREIGN KEY(position_id) REFERENCES user_positions(id) ON DELETE CASCADE,
                    FOREIGN KEY(alert_id) REFERENCES alert_logs(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS user_alert_actions (
                    alert_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(alert_id, user_id, action),
                    FOREIGN KEY(alert_id) REFERENCES alert_logs(id) ON DELETE CASCADE,
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS classifier_examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    example_text TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE
                );
                """
            )
            self._ensure_column(conn, "analysts", "discord_user_id", "INTEGER")
            self._ensure_column(conn, "guilds", "is_active", "INTEGER NOT NULL DEFAULT 1")
            self._ensure_column(conn, "guilds", "disabled_reason", "TEXT")
            self._ensure_column(conn, "alert_logs", "trade_note", "TEXT")
            self._ensure_column(conn, "alert_logs", "status", "TEXT NOT NULL DEFAULT 'open'")
            self._ensure_column(conn, "alert_logs", "old_contract", "TEXT")
            self._ensure_column(conn, "alert_logs", "old_expiration", "TEXT")
            self._ensure_column(conn, "alert_logs", "old_price", "REAL")
            self._ensure_column(conn, "alert_logs", "roll_cost", "REAL")
            self._ensure_column(conn, "alert_logs", "roll_cost_type", "TEXT")
            self._ensure_column(conn, "alert_logs", "parent_alert_id", "INTEGER")
            self._ensure_column(conn, "user_positions", "current_alert_id", "INTEGER")
            self._ensure_column(conn, "user_positions", "average_price", "REAL")
            self._ensure_column(conn, "user_positions", "add_count", "INTEGER NOT NULL DEFAULT 1")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _row_to_analyst(self, row: sqlite3.Row) -> Analyst:
        return Analyst(
            id=row["id"],
            guild_id=row["guild_id"],
            name=row["name"],
            is_active=bool(row["is_active"]),
            discord_user_id=row["discord_user_id"],
        )

    def ensure_guild(self, guild_id: int) -> None:
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO guilds (guild_id) VALUES (?)", (guild_id,))

    def is_guild_active(self, guild_id: int) -> bool:
        self.ensure_guild(guild_id)
        with self.connect() as conn:
            row = conn.execute("SELECT is_active FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone()
        return bool(row and row["is_active"])

    def set_guild_active(self, guild_id: int, is_active: bool, reason: Optional[str] = None) -> None:
        self.ensure_guild(guild_id)
        with self.connect() as conn:
            conn.execute(
                "UPDATE guilds SET is_active = ?, disabled_reason = ? WHERE guild_id = ?",
                (1 if is_active else 0, None if is_active else reason, guild_id),
            )

    def list_guild_statuses(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT guild_id, is_active, disabled_reason FROM guilds ORDER BY guild_id"
            ).fetchall()

    def set_review_channel(self, guild_id: int, channel_id: int) -> None:
        self.ensure_guild(guild_id)
        with self.connect() as conn:
            conn.execute("UPDATE guilds SET review_channel_id = ? WHERE guild_id = ?", (channel_id, guild_id))

    def get_review_channel_id(self, guild_id: int) -> Optional[int]:
        self.ensure_guild(guild_id)
        with self.connect() as conn:
            row = conn.execute("SELECT review_channel_id FROM guilds WHERE guild_id = ?", (guild_id,)).fetchone()
        return int(row["review_channel_id"]) if row and row["review_channel_id"] else None

    def add_analyst(self, guild_id: int, name: str) -> None:
        self.ensure_guild(guild_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analysts (guild_id, name, is_active)
                VALUES (?, ?, 1)
                ON CONFLICT(guild_id, name) DO UPDATE SET is_active = 1
                """,
                (guild_id, name.strip()),
            )

    def add_analyst_user(self, guild_id: int, discord_user_id: int, display_name: str) -> None:
        self.ensure_guild(guild_id)
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM analysts
                WHERE guild_id = ? AND (discord_user_id = ? OR name = ?)
                """,
                (guild_id, discord_user_id, str(discord_user_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE analysts SET name = ?, discord_user_id = ?, is_active = 1 WHERE id = ?",
                    (display_name.strip(), discord_user_id, existing["id"]),
                )
                conn.execute(
                    """
                    UPDATE analysts
                    SET is_active = 0
                    WHERE guild_id = ?
                    AND id != ?
                    AND (
                        lower(name) = lower(?)
                        OR discord_user_id = ?
                        OR name = ?
                        OR name = ?
                    )
                    """,
                    (
                        guild_id,
                        existing["id"],
                        display_name.strip(),
                        discord_user_id,
                        str(discord_user_id),
                        f"<@{discord_user_id}>",
                    ),
                )
                return

            conn.execute(
                """
                INSERT INTO analysts (guild_id, name, discord_user_id, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(guild_id, name) DO UPDATE SET
                    discord_user_id = excluded.discord_user_id,
                    is_active = 1
                """,
                (guild_id, display_name.strip(), discord_user_id),
            )
            new_row = conn.execute(
                "SELECT id FROM analysts WHERE guild_id = ? AND discord_user_id = ? AND is_active = 1",
                (guild_id, discord_user_id),
            ).fetchone()
            if new_row:
                conn.execute(
                    """
                    UPDATE analysts
                    SET is_active = 0
                    WHERE guild_id = ?
                    AND id != ?
                    AND (
                        lower(name) = lower(?)
                        OR discord_user_id = ?
                        OR name = ?
                        OR name = ?
                    )
                    """,
                    (
                        guild_id,
                        new_row["id"],
                        display_name.strip(),
                        discord_user_id,
                        str(discord_user_id),
                        f"<@{discord_user_id}>",
                    ),
                )

    def remove_analyst(self, guild_id: int, name: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE analysts SET is_active = 0 WHERE guild_id = ? AND lower(name) = lower(?)",
                (guild_id, name.strip()),
            )
            return cur.rowcount > 0

    def remove_analyst_user(self, guild_id: int, discord_user_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE analysts SET is_active = 0
                WHERE guild_id = ? AND (discord_user_id = ? OR name = ?)
                """,
                (guild_id, discord_user_id, str(discord_user_id)),
            )
            return cur.rowcount > 0

    def get_analyst_by_name(self, guild_id: int, name: str) -> Optional[Analyst]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM analysts WHERE guild_id = ? AND lower(name) = lower(?) AND is_active = 1",
                (guild_id, name.strip()),
            ).fetchone()
        return self._row_to_analyst(row) if row else None

    def get_analyst_by_user_id(self, guild_id: int, discord_user_id: int) -> Optional[Analyst]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM analysts
                WHERE guild_id = ? AND is_active = 1
                AND (
                    discord_user_id = ?
                    OR name = ?
                    OR name = ?
                    OR name = ?
                )
                """,
                (
                    guild_id,
                    discord_user_id,
                    str(discord_user_id),
                    f"<@{discord_user_id}>",
                    f"<@!{discord_user_id}>",
                ),
            ).fetchone()
        return self._row_to_analyst(row) if row else None

    def list_analysts(self, guild_id: int) -> list[Analyst]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM analysts WHERE guild_id = ? AND is_active = 1 ORDER BY name",
                (guild_id,),
            ).fetchall()
        return [self._row_to_analyst(row) for row in rows]

    def set_analyst_channel(self, guild_id: int, analyst_id: int, channel_id: int) -> None:
        self.ensure_guild(guild_id)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO analyst_channels (guild_id, analyst_id, channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, analyst_id) DO UPDATE SET channel_id = excluded.channel_id
                """,
                (guild_id, analyst_id, channel_id),
            )

    def get_analyst_for_channel(self, guild_id: int, channel_id: int) -> Optional[Analyst]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT a.* FROM analysts a
                JOIN analyst_channels ac ON ac.analyst_id = a.id
                WHERE ac.guild_id = ? AND ac.channel_id = ? AND a.is_active = 1
                """,
                (guild_id, channel_id),
            ).fetchone()
        return self._row_to_analyst(row) if row else None

    def get_channel_map(self, guild_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT a.name, ac.channel_id
                FROM analyst_channels ac
                JOIN analysts a ON a.id = ac.analyst_id
                WHERE ac.guild_id = ? AND a.is_active = 1
                ORDER BY a.name
                """,
                (guild_id,),
            ).fetchall()

    def replace_subscriptions(self, guild_id: int, user_id: int, analyst_ids: Iterable[int]) -> None:
        self.ensure_guild(guild_id)
        ids = list(analyst_ids)
        with self.connect() as conn:
            conn.execute("DELETE FROM user_subscriptions WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            conn.executemany(
                "INSERT INTO user_subscriptions (guild_id, user_id, analyst_id) VALUES (?, ?, ?)",
                [(guild_id, user_id, analyst_id) for analyst_id in ids],
            )

    def list_user_subscriptions(self, guild_id: int, user_id: int) -> list[Analyst]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT a.* FROM user_subscriptions us
                JOIN analysts a ON a.id = us.analyst_id
                WHERE us.guild_id = ? AND us.user_id = ? AND a.is_active = 1
                ORDER BY a.name
                """,
                (guild_id, user_id),
            ).fetchall()
        return [self._row_to_analyst(row) for row in rows]

    def set_user_pause(self, guild_id: int, user_id: int, is_paused: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE user_subscriptions SET is_paused = ? WHERE guild_id = ? AND user_id = ?",
                (1 if is_paused else 0, guild_id, user_id),
            )

    def subscribed_users(self, guild_id: int, analyst_id: int) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT user_id FROM user_subscriptions
                WHERE guild_id = ? AND analyst_id = ? AND is_paused = 0
                """,
                (guild_id, analyst_id),
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def add_classifier_example(self, guild_id: int, action: str, example_text: str) -> int:
        self.ensure_guild(guild_id)
        if action in {"exit", "stop"}:
            action = "close"
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO classifier_examples (guild_id, action, example_text)
                VALUES (?, ?, ?)
                """,
                (guild_id, action, example_text.strip()),
            )
            return int(cur.lastrowid)

    def add_classifier_examples(self, guild_id: int, examples: dict[str, list[str]]) -> int:
        self.ensure_guild(guild_id)
        rows = [
            (guild_id, "close" if action in {"exit", "stop"} else action, example_text.strip())
            for action, texts in examples.items()
            for example_text in texts
            if example_text and example_text.strip()
        ]
        if not rows:
            return 0

        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO classifier_examples (guild_id, action, example_text)
                VALUES (?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def list_classifier_examples(self, guild_id: int, limit: int = 60) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT id, action, example_text, created_at
                FROM classifier_examples
                WHERE guild_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()

    def delete_classifier_example(self, guild_id: int, example_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "DELETE FROM classifier_examples WHERE guild_id = ? AND id = ?",
                (guild_id, example_id),
            )
            return cur.rowcount > 0

    def log_alert(
        self,
        guild_id: int,
        analyst_id: int,
        channel_id: Optional[int],
        message_id: Optional[int],
        parsed: ParsedAlert,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO alert_logs
                (
                    guild_id, analyst_id, channel_id, message_id, action, confidence,
                    ticker, contract, expiration, price, old_contract, old_expiration,
                    old_price, roll_cost, roll_cost_type, trade_note, raw_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    guild_id,
                    analyst_id,
                    channel_id,
                    message_id,
                    parsed.action,
                    parsed.confidence,
                    parsed.ticker,
                    parsed.contract,
                    parsed.expiration,
                    parsed.price,
                    parsed.old_contract,
                    parsed.old_expiration,
                    parsed.old_price,
                    parsed.roll_cost,
                    parsed.roll_cost_type,
                    parsed.trade_note,
                    parsed.raw_text,
                ),
            )
            return int(cur.lastrowid)

    def is_alert_open(self, alert_id: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM alert_logs WHERE id = ? AND action IN ('entry', 'roll_option')",
                (alert_id,),
            ).fetchone()
        return bool(row and (row["status"] or "open") == "open")

    def latest_open_entry_alert(
        self,
        guild_id: int,
        analyst_id: int,
        ticker: Optional[str] = None,
        contract: Optional[str] = None,
    ) -> Optional[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM alert_logs
                WHERE guild_id = ? AND analyst_id = ? AND action IN ('entry', 'roll_option')
                AND COALESCE(status, 'open') = 'open'
                AND (? IS NULL OR ticker = ?)
                AND (? IS NULL OR contract = ?)
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (guild_id, analyst_id, ticker, ticker, contract, contract),
            ).fetchone()

    def list_open_entry_alerts(self, guild_id: int, analyst_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM alert_logs
                WHERE guild_id = ? AND analyst_id = ? AND action IN ('entry', 'roll_option')
                AND COALESCE(status, 'open') = 'open'
                ORDER BY created_at DESC, id DESC
                """,
                (guild_id, analyst_id),
            ).fetchall()

    def list_open_entry_alerts_for_analyst_user(
        self,
        guild_id: int,
        discord_user_id: int,
        names: Iterable[str] = (),
    ) -> list[sqlite3.Row]:
        clean_names = [name.strip() for name in names if name and name.strip()]
        mention_names = [str(discord_user_id), f"<@{discord_user_id}>", f"<@!{discord_user_id}>"]
        all_names = list(dict.fromkeys(clean_names + mention_names))
        placeholders = ",".join("?" for _ in all_names) or "NULL"

        with self.connect() as conn:
            return conn.execute(
                f"""
                SELECT al.* FROM alert_logs al
                JOIN analysts a ON a.id = al.analyst_id
                WHERE al.guild_id = ?
                AND al.action IN ('entry', 'roll_option')
                AND COALESCE(al.status, 'open') = 'open'
                AND (
                    a.discord_user_id = ?
                    OR a.name IN ({placeholders})
                    OR lower(a.name) IN ({placeholders})
                )
                ORDER BY al.created_at DESC, al.id DESC
                """,
                (
                    guild_id,
                    discord_user_id,
                    *all_names,
                    *[name.lower() for name in all_names],
                ),
            ).fetchall()

    def close_entry_alert(self, alert_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE alert_logs SET status = 'closed' WHERE id = ? AND action IN ('entry', 'roll_option')",
                (alert_id,),
            )

    def close_all_entry_alerts(self, guild_id: int, analyst_id: int) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE alert_logs
                SET status = 'closed'
                WHERE guild_id = ? AND analyst_id = ? AND action IN ('entry', 'roll_option')
                AND COALESCE(status, 'open') = 'open'
                """,
                (guild_id, analyst_id),
            )
            return cur.rowcount

    def close_all_user_positions(self, guild_id: int, analyst_id: int) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE user_positions
                SET status = 'closed', closed_at = CURRENT_TIMESTAMP
                WHERE guild_id = ? AND analyst_id = ? AND status = 'open'
                """,
                (guild_id, analyst_id),
            )
            return cur.rowcount

    def close_matching_entry_alerts(
        self,
        guild_id: int,
        analyst_id: int,
        ticker: Optional[str] = None,
        contract: Optional[str] = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE alert_logs
                SET status = 'closed'
                WHERE guild_id = ? AND analyst_id = ? AND action IN ('entry', 'roll_option')
                AND COALESCE(status, 'open') = 'open'
                AND (? IS NULL OR ticker = ?)
                AND (? IS NULL OR contract = ?)
                """,
                (guild_id, analyst_id, ticker, ticker, contract, contract),
            )
            return cur.rowcount

    def mark_entry_alert_rolled(self, old_alert_id: int, new_alert_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE alert_logs SET status = 'rolled' WHERE id = ? AND action IN ('entry', 'roll_option')",
                (old_alert_id,),
            )
            conn.execute(
                "UPDATE alert_logs SET parent_alert_id = ?, status = 'open' WHERE id = ?",
                (old_alert_id, new_alert_id),
            )

    def update_roll_alert_details(
        self,
        alert_id: int,
        old_contract: Optional[str],
        old_expiration: Optional[str],
        old_price: Optional[float],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE alert_logs
                SET old_contract = COALESCE(old_contract, ?),
                    old_expiration = COALESCE(old_expiration, ?),
                    old_price = COALESCE(old_price, ?)
                WHERE id = ?
                """,
                (old_contract, old_expiration, old_price, alert_id),
            )

    def mark_alert_action(self, alert_id: int, guild_id: int, user_id: int, action: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO user_alert_actions (alert_id, guild_id, user_id, action)
                VALUES (?, ?, ?, ?)
                """,
                (alert_id, guild_id, user_id, action),
            )

    def open_position(self, guild_id: int, user_id: int, analyst_id: int, alert_id: int) -> int:
        with self.connect() as conn:
            alert = conn.execute("SELECT * FROM alert_logs WHERE id = ?", (alert_id,)).fetchone()
            cur = conn.execute(
                """
                INSERT INTO user_positions
                (guild_id, user_id, analyst_id, entry_alert_id, current_alert_id, ticker, contract, expiration, entry_price, average_price, add_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    guild_id,
                    user_id,
                    analyst_id,
                    alert_id,
                    alert_id,
                    alert["ticker"] if alert else None,
                    alert["contract"] if alert else None,
                    alert["expiration"] if alert else None,
                    alert["price"] if alert else None,
                    alert["price"] if alert else None,
                ),
            )
            return int(cur.lastrowid)

    def find_open_positions_for_entry_alert(
        self,
        guild_id: int,
        user_id: int,
        entry_alert_id: int,
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT * FROM user_positions
                WHERE guild_id = ? AND user_id = ? AND (entry_alert_id = ? OR current_alert_id = ?) AND status = 'open'
                ORDER BY opened_at DESC
                """,
                (guild_id, user_id, entry_alert_id, entry_alert_id),
            ).fetchall()

    def find_open_positions(
        self,
        guild_id: int,
        user_id: int,
        analyst_id: int,
        ticker: Optional[str],
        contract: Optional[str],
    ) -> list[sqlite3.Row]:
        with self.connect() as conn:
            if ticker or contract:
                return conn.execute(
                    """
                    SELECT * FROM user_positions
                    WHERE guild_id = ? AND user_id = ? AND analyst_id = ? AND status = 'open'
                    AND (? IS NULL OR ticker = ?)
                    AND (? IS NULL OR contract = ?)
                    ORDER BY opened_at DESC
                    """,
                    (guild_id, user_id, analyst_id, ticker, ticker, contract, contract),
                ).fetchall()

            row = conn.execute(
                """
                SELECT * FROM user_positions
                WHERE guild_id = ? AND user_id = ? AND analyst_id = ? AND status = 'open'
                ORDER BY opened_at DESC
                LIMIT 1
                """,
                (guild_id, user_id, analyst_id),
            ).fetchall()
            return row

    def list_open_positions(self, guild_id: int, user_id: int) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                SELECT up.*, a.name AS analyst_name
                FROM user_positions up
                JOIN analysts a ON a.id = up.analyst_id
                WHERE up.guild_id = ? AND up.user_id = ? AND up.status = 'open'
                ORDER BY up.opened_at DESC
                """,
                (guild_id, user_id),
            ).fetchall()

    def record_average_event(
        self,
        position_id: int,
        user_id: int,
        alert_id: int,
        event_type: str,
        price: Optional[float],
        raw_text: str,
    ) -> bool:
        with self.connect() as conn:
            position = conn.execute(
                "SELECT * FROM user_positions WHERE id = ? AND user_id = ? AND status = 'open'",
                (position_id, user_id),
            ).fetchone()
            if not position:
                return False

            old_average = position["average_price"] if position["average_price"] is not None else position["entry_price"]
            add_count = int(position["add_count"] or 1)
            new_average = old_average
            if price is not None and old_average is not None:
                new_average = ((float(old_average) * add_count) + price) / (add_count + 1)
                add_count += 1
            elif price is not None:
                new_average = price
                add_count += 1

            conn.execute(
                """
                UPDATE user_positions
                SET average_price = ?, add_count = ?
                WHERE id = ? AND user_id = ?
                """,
                (new_average, add_count, position_id, user_id),
            )
            conn.execute(
                """
                INSERT INTO position_events
                (
                    guild_id, user_id, analyst_id, position_id, alert_id, event_type,
                    ticker, old_contract, old_expiration, old_price,
                    new_contract, new_expiration, new_price, raw_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position["guild_id"],
                    user_id,
                    position["analyst_id"],
                    position_id,
                    alert_id,
                    event_type,
                    position["ticker"],
                    position["contract"],
                    position["expiration"],
                    old_average,
                    position["contract"],
                    position["expiration"],
                    price,
                    raw_text,
                ),
            )
            return True

    def roll_position(
        self,
        position_id: int,
        user_id: int,
        alert_id: int,
        ticker: Optional[str],
        new_contract: Optional[str],
        new_expiration: Optional[str],
        new_price: Optional[float],
        roll_cost: Optional[float],
        roll_cost_type: Optional[str],
        raw_text: str,
    ) -> bool:
        with self.connect() as conn:
            position = conn.execute(
                "SELECT * FROM user_positions WHERE id = ? AND user_id = ? AND status = 'open'",
                (position_id, user_id),
            ).fetchone()
            if not position:
                return False

            next_ticker = ticker or position["ticker"]
            next_contract = new_contract or position["contract"]
            next_expiration = new_expiration or position["expiration"]
            next_price = new_price if new_price is not None else position["average_price"] or position["entry_price"]

            conn.execute(
                """
                INSERT INTO position_events
                (
                    guild_id, user_id, analyst_id, position_id, alert_id, event_type,
                    ticker, old_contract, old_expiration, old_price,
                    new_contract, new_expiration, new_price, roll_cost, roll_cost_type, raw_text
                )
                VALUES (?, ?, ?, ?, ?, 'roll_option', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position["guild_id"],
                    user_id,
                    position["analyst_id"],
                    position_id,
                    alert_id,
                    position["ticker"],
                    position["contract"],
                    position["expiration"],
                    position["average_price"] if position["average_price"] is not None else position["entry_price"],
                    next_contract,
                    next_expiration,
                    new_price,
                    roll_cost,
                    roll_cost_type,
                    raw_text,
                ),
            )
            conn.execute(
                """
                UPDATE user_positions
                SET ticker = ?, contract = ?, expiration = ?, entry_price = ?,
                    average_price = ?, add_count = 1, current_alert_id = ?
                WHERE id = ? AND user_id = ?
                """,
                (next_ticker, next_contract, next_expiration, next_price, next_price, alert_id, position_id, user_id),
            )
            return True

    def close_position(self, position_id: int, user_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                """
                UPDATE user_positions
                SET status = 'closed', closed_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ? AND status = 'open'
                """,
                (position_id, user_id),
            )
            return cur.rowcount > 0
