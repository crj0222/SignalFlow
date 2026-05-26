from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

from dotenv import load_dotenv

from analytics import AnalystStats, build_analyst_stats, build_trade_records, format_pct, format_trade_result
from database import Database


load_dotenv()

DATABASE_PATH = os.getenv("DATABASE_PATH", "signalflow.sqlite3").strip() or "signalflow.sqlite3"


def _dashboard_host() -> str:
    configured = os.getenv("DASHBOARD_HOST", "").strip()
    if os.getenv("PORT") and configured in {"", "127.0.0.1", "localhost", "::1"}:
        return "0.0.0.0"
    return configured or "127.0.0.1"


DASHBOARD_HOST = _dashboard_host()
DASHBOARD_PORT = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or "8080")
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "").strip()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
PUBLIC_DASHBOARD_URL = os.getenv("PUBLIC_DASHBOARD_URL", "").strip().rstrip("/")
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "").strip()
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
DISCORD_OAUTH_REDIRECT_URI = os.getenv("DISCORD_OAUTH_REDIRECT_URI", "").strip()
DASHBOARD_SESSION_SECRET = (
    os.getenv("DASHBOARD_SESSION_SECRET", "").strip()
    or DASHBOARD_TOKEN
    or DISCORD_CLIENT_SECRET
    or "signalflow-local-dashboard-secret"
)
OWNER_IDS = {int(item.strip()) for item in os.getenv("OWNER_IDS", "").split(",") if item.strip().isdigit()}

DISCORD_API_BASE = "https://discord.com/api/v10"
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8
SESSION_COOKIE = "sf_dashboard_session"
STATE_COOKIE = "sf_oauth_state"
SIGNED_SESSION_PREFIX = "sfv1."
SESSION_SECONDS = 7 * 24 * 60 * 60
STATE_SECONDS = 10 * 60
EXAMPLE_ACTIONS = ("entry", "trim", "close", "ignore")

DB = Database(DATABASE_PATH)
USER_NAME_CACHE: dict[int, str] = {}
CHANNEL_NAME_CACHE: dict[int, str] = {}
GUILD_NAME_CACHE: dict[int, str] = {}


@dataclass(frozen=True)
class AuthContext:
    ok: bool
    mode: str = "none"
    token: str = ""
    user_id: Optional[int] = None
    username: str = ""
    avatar_url: str = ""
    allowed_guild_ids: tuple[int, ...] = ()
    reason: str = ""

    @property
    def is_oauth(self) -> bool:
        return self.mode == "oauth"

    @property
    def is_owner_token(self) -> bool:
        return self.mode == "owner_token"


def _ensure_dashboard_tables() -> None:
    with DB.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dashboard_sessions (
                session_token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                avatar_url TEXT,
                allowed_guild_ids TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_dashboard_sessions_expires
                ON dashboard_sessions(expires_at);
            """
        )


_ensure_dashboard_tables()


def _oauth_configured() -> bool:
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and _oauth_redirect_uri())


def _base_url() -> str:
    if PUBLIC_DASHBOARD_URL:
        return PUBLIC_DASHBOARD_URL
    host = "127.0.0.1" if DASHBOARD_HOST in {"0.0.0.0", "::"} else DASHBOARD_HOST
    return f"http://{host}:{DASHBOARD_PORT}"


def _oauth_redirect_uri() -> str:
    return DISCORD_OAUTH_REDIRECT_URI or f"{_base_url()}/oauth/callback"


def _secure_cookie() -> bool:
    return _base_url().startswith("https://")


def _all_guild_ids() -> tuple[int, ...]:
    path = Path(DATABASE_PATH)
    if not path.exists():
        return ()
    with sqlite3.connect(path) as conn:
        rows = conn.execute("SELECT guild_id FROM guilds ORDER BY created_at DESC").fetchall()
    return tuple(int(row[0]) for row in rows)


def _guild_settings(guild_id: Optional[int]) -> Optional[sqlite3.Row]:
    return DB.get_guild_settings(guild_id) if guild_id else None


def _configured_guild_rows(allowed_ids: Optional[tuple[int, ...]] = None) -> list[sqlite3.Row]:
    allowed = set(allowed_ids or ())
    with DB.connect() as conn:
        rows = conn.execute("SELECT * FROM guilds ORDER BY created_at DESC").fetchall()
    if allowed_ids is None:
        return rows
    return [row for row in rows if int(row["guild_id"]) in allowed]


def _guild_name(guild_id: Optional[int], settings: Optional[sqlite3.Row] = None) -> str:
    if settings and settings["dashboard_display_name"]:
        return str(settings["dashboard_display_name"])
    if settings and settings["guild_name"]:
        return str(settings["guild_name"])
    if guild_id:
        discord_name = _fetch_guild_name(guild_id)
        if discord_name:
            return discord_name
    return f"Server {guild_id}" if guild_id else "SignalFlow"


def _record(stats: AnalystStats) -> str:
    if stats.breakevens:
        return f"{stats.wins}W / {stats.losses}L / {stats.breakevens}B/E"
    return f"{stats.wins}W / {stats.losses}L"


def _stats_to_dict(stats: AnalystStats) -> dict[str, object]:
    return {
        "analyst_id": stats.analyst_id,
        "analyst_name": stats.analyst_name,
        "closed_trades": stats.closed_trades,
        "wins": stats.wins,
        "losses": stats.losses,
        "breakevens": stats.breakevens,
        "win_rate": stats.win_rate,
        "avg_return_pct": stats.avg_return_pct,
        "avg_win_pct": stats.avg_win_pct,
        "avg_loss_pct": stats.avg_loss_pct,
        "stop_out_rate": stats.stop_out_rate,
        "trim_rate": stats.trim_rate,
        "open_trades": stats.open_trades,
        "best_trade": format_trade_result(stats.best_trade),
        "worst_trade": format_trade_result(stats.worst_trade),
        "asset_breakdown": {
            key: {
                "closed": value.closed,
                "wins": value.wins,
                "losses": value.losses,
                "avg_return_pct": value.avg_return_pct,
            }
            for key, value in stats.asset_breakdown.items()
        },
    }


def _server_summary(stats: list[AnalystStats]) -> dict[str, object]:
    closed = sum(item.closed_trades for item in stats)
    wins = sum(item.wins for item in stats)
    losses = sum(item.losses for item in stats)
    breakevens = sum(item.breakevens for item in stats)
    open_trades = sum(item.open_trades for item in stats)
    win_rate = (wins / (wins + losses) * 100) if wins or losses else None
    best = max((item.best_trade for item in stats if item.best_trade), key=lambda trade: trade.result_pct or -10**9, default=None)
    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "open_trades": open_trades,
        "win_rate": win_rate,
        "best_trade": format_trade_result(best),
    }


def _asset_pills(stats: AnalystStats) -> str:
    labels = {"option": "Options", "stock": "Stocks", "future": "Futures", "unknown": "Other"}
    pills = []
    for key, label in labels.items():
        asset = stats.asset_breakdown.get(key)
        if not asset:
            continue
        pills.append(
            f'<span class="pill">{html.escape(label)} '
            f'<strong>{asset.wins}W/{asset.losses}L</strong> '
            f'<em>{format_pct(asset.avg_return_pct)}</em></span>'
        )
    return "".join(pills) or '<span class="muted">No closed trades by type yet</span>'


def _action_url(guild_id: Optional[int], token: str = "", message: str = "") -> str:
    params = {}
    if guild_id:
        params["guild_id"] = str(guild_id)
    if token:
        params["token"] = token
    if message:
        params["message"] = message
    return "/?" + urlencode(params) if params else "/"


def _hidden_inputs(guild_id: Optional[int], token: str = "") -> str:
    token_input = f'<input type="hidden" name="token" value="{html.escape(token)}">' if token else ""
    return f'<input type="hidden" name="guild_id" value="{html.escape(str(guild_id or ""))}">{token_input}'


def _api_url(guild_id: Optional[int], token: str = "") -> str:
    params = {}
    if guild_id:
        params["guild_id"] = str(guild_id)
    if token:
        params["token"] = token
    return "/api/stats" + (f"?{urlencode(params)}" if params else "")


def _analyst_options(analysts, selected_id: Optional[int] = None) -> str:
    if not analysts:
        return '<option value="">No analysts configured</option>'
    options = []
    for analyst in analysts:
        selected = " selected" if selected_id == analyst.id else ""
        options.append(f'<option value="{analyst.id}"{selected}>{html.escape(_analyst_display_name(analyst))}</option>')
    return "\n".join(options)


def _render_stats_table(stats: list[AnalystStats]) -> str:
    rows = []
    for item in stats:
        avg_class = "good" if item.avg_return_pct is not None and item.avg_return_pct >= 0 else "bad"
        analyst_name = _display_name_from_text(item.analyst_name)
        rows.append(
            f"""
            <tr>
                <td><strong>{html.escape(analyst_name)}</strong><div class="sub">{_asset_pills(item)}</div></td>
                <td>{html.escape(_record(item))}</td>
                <td>{item.closed_trades}</td>
                <td>{format_pct(item.win_rate)}</td>
                <td class="{avg_class}">{format_pct(item.avg_return_pct)}</td>
                <td>{format_pct(item.avg_win_pct)}</td>
                <td>{format_pct(item.avg_loss_pct)}</td>
                <td>{format_pct(item.stop_out_rate)}</td>
                <td>{item.open_trades}</td>
                <td>{html.escape(format_trade_result(item.best_trade))}</td>
            </tr>
            """
        )
    return "\n".join(rows) if rows else '<tr><td colspan="10" class="empty">No analyst stats yet. Close a tracked trade first.</td></tr>'


def _render_analyst_list(analysts) -> str:
    rows = []
    for analyst in analysts:
        user_id = f"<code>{analyst.discord_user_id}</code>" if analyst.discord_user_id else '<span class="muted">No user ID</span>'
        rows.append(f'<li><strong>{html.escape(_analyst_display_name(analyst))}</strong><span>{user_id}</span></li>')
    return "\n".join(rows) if rows else '<li class="empty-list">No analysts configured yet.</li>'


def _render_channel_rows(guild_id: int, channel_map, hidden: str, action_url: str) -> str:
    rows = []
    for row in channel_map:
        channel_id = int(row["channel_id"])
        channel_name = _channel_display_name(guild_id, channel_id, row["channel_name"] if "channel_name" in row.keys() else None)
        analyst_name = _analyst_row_display_name(row)
        rows.append(
            f"""
            <tr>
              <td><strong>{html.escape(analyst_name)}</strong></td>
              <td><strong>{html.escape(channel_name)}</strong><div class="sub"><code>{channel_id}</code></div></td>
              <td class="right">
                <form method="post" action="{action_url}" class="inline-form">
                  {hidden}
                  <input type="hidden" name="action" value="remove_channel">
                  <input type="hidden" name="channel_id" value="{channel_id}">
                  <button class="ghost danger-text" type="submit">Remove</button>
                </form>
              </td>
            </tr>
            """
        )
    return "\n".join(rows) if rows else '<tr><td colspan="3" class="empty">No channels mapped yet.</td></tr>'


def _render_example_rows(examples, hidden: str, action_url: str) -> str:
    rows = []
    for row in examples:
        text = " ".join(str(row["example_text"]).split())
        if len(text) > 150:
            text = text[:147] + "..."
        rows.append(
            f"""
            <tr>
              <td>#{int(row["id"])}</td>
              <td><span class="tag">{html.escape(str(row["action"]))}</span></td>
              <td>{html.escape(text)}</td>
              <td class="right">
                <form method="post" action="{action_url}" class="inline-form">
                  {hidden}
                  <input type="hidden" name="action" value="remove_example">
                  <input type="hidden" name="example_id" value="{int(row["id"])}">
                  <button class="ghost danger-text" type="submit">Remove</button>
                </form>
              </td>
            </tr>
            """
        )
    return "\n".join(rows) if rows else '<tr><td colspan="4" class="empty">No classifier examples yet.</td></tr>'


def _render_open_trades_rows(open_trades) -> str:
    rows = []
    for trade in open_trades:
        price = f"{trade.entry_price:g}" if trade.entry_price is not None else "N/A"
        analyst_name = _display_name_from_text(trade.analyst_name)
        rows.append(
            f"""
            <tr>
              <td><strong>{html.escape(analyst_name)}</strong></td>
              <td>{html.escape(trade.symbol)}</td>
              <td><span class="tag">{html.escape(trade.asset_type.title())}</span></td>
              <td>{html.escape(price)}</td>
              <td>{html.escape(trade.trade_note or "")}</td>
              <td>{html.escape(trade.opened_at)}</td>
            </tr>
            """
        )
    return "\n".join(rows) if rows else '<tr><td colspan="6" class="empty">No open analyst trades.</td></tr>'


def _render_import_rows(imports) -> str:
    rows = []
    for row in imports:
        rows.append(
            f'<li><strong>{html.escape(str(row["filename"]))}</strong>'
            f'<span>{html.escape(str(row["action"]))} - saved {int(row["saved_count"] or 0)} / scanned {int(row["scanned_count"] or 0)}</span></li>'
        )
    return "\n".join(rows) if rows else '<li class="empty-list">No active imported files tracked yet.</li>'


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign_json(payload: dict[str, object]) -> str:
    encoded = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(DASHBOARD_SESSION_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _verify_signed_json(value: str) -> Optional[dict[str, object]]:
    try:
        encoded, signature = value.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(DASHBOARD_SESSION_SECRET.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        return json.loads(_unb64(encoded).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None


def _cookie(name: str, value: str, max_age: int, path: str = "/") -> str:
    secure = "; Secure" if _secure_cookie() else ""
    return f"{name}={value}; Max-Age={max_age}; Path={path}; HttpOnly; SameSite=Lax{secure}"


def _expired_cookie(name: str, path: str = "/") -> str:
    secure = "; Secure" if _secure_cookie() else ""
    return f"{name}=; Max-Age=0; Path={path}; HttpOnly; SameSite=Lax{secure}"


def _parse_cookie(header: str, name: str) -> str:
    cookie = SimpleCookie()
    cookie.load(header or "")
    item = cookie.get(name)
    return item.value if item else ""


def _append_query(path: str, params: dict[str, str]) -> str:
    clean = _sanitize_next(path)
    separator = "&" if "?" in clean else "?"
    return clean + separator + urlencode(params)


def _sanitize_next(next_path: str) -> str:
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return "/"
    return next_path


def _discord_request(path: str, access_token: str) -> dict | list:
    request = urllib.request.Request(
        f"{DISCORD_API_BASE}{path}",
        headers={"Authorization": f"Bearer {access_token}", "User-Agent": "SignalFlowDashboard/1.0"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _discord_bot_request(path: str) -> Optional[dict | list]:
    if not DISCORD_BOT_TOKEN:
        return None
    request = urllib.request.Request(
        f"{DISCORD_API_BASE}{path}",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "User-Agent": "SignalFlowDashboard/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, (dict, list)) else None
    except (urllib.error.URLError, json.JSONDecodeError):
        return None


def _discord_guild_icon_url(guild_id: int, icon_hash: object) -> str:
    icon = str(icon_hash or "")
    return f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png?size=128" if icon else ""


def _fetch_guild_name(guild_id: int) -> str:
    if guild_id in GUILD_NAME_CACHE:
        return GUILD_NAME_CACHE[guild_id]
    payload = _discord_bot_request(f"/guilds/{guild_id}")
    name = str(payload.get("name") or "").strip() if isinstance(payload, dict) else ""
    if name:
        GUILD_NAME_CACHE[guild_id] = name
        DB.update_guild_metadata(guild_id, name, _discord_guild_icon_url(guild_id, payload.get("icon")))
    return name


def _fetch_channel_name(guild_id: int, channel_id: int) -> str:
    if channel_id in CHANNEL_NAME_CACHE:
        return CHANNEL_NAME_CACHE[channel_id]
    payload = _discord_bot_request(f"/channels/{channel_id}")
    name = str(payload.get("name") or "").strip() if isinstance(payload, dict) else ""
    if name:
        CHANNEL_NAME_CACHE[channel_id] = name
        DB.update_analyst_channel_name(guild_id, channel_id, name)
    return name


def _fetch_user_name(user_id: int) -> str:
    if user_id in USER_NAME_CACHE:
        return USER_NAME_CACHE[user_id]
    payload = _discord_bot_request(f"/users/{user_id}")
    name = ""
    if isinstance(payload, dict):
        name = str(payload.get("global_name") or payload.get("username") or "").strip()
    if name:
        USER_NAME_CACHE[user_id] = name
    return name


def _snowflake_from_name(raw_name: str) -> Optional[int]:
    text = (raw_name or "").strip()
    mention = re.fullmatch(r"<@!?(\d+)>", text)
    if mention:
        return int(mention.group(1))
    return int(text) if text.isdigit() and len(text) >= 15 else None


def _analyst_display_name(analyst) -> str:
    user_id = getattr(analyst, "discord_user_id", None) or _snowflake_from_name(getattr(analyst, "name", ""))
    if user_id:
        fetched = _fetch_user_name(int(user_id))
        if fetched:
            return fetched
    name = str(getattr(analyst, "name", "")).strip()
    return _display_name_from_text(name)


def _display_name_from_text(raw_name: str) -> str:
    user_id = _snowflake_from_name(raw_name)
    if user_id:
        fetched = _fetch_user_name(user_id)
        if fetched:
            return fetched
    name = str(raw_name or "").strip()
    return name[1:] if name.startswith("@") else name


def _analyst_row_display_name(row: sqlite3.Row) -> str:
    user_id = row["discord_user_id"] if "discord_user_id" in row.keys() else None
    if user_id:
        fetched = _fetch_user_name(int(user_id))
        if fetched:
            return fetched
    raw_name = str(row["name"] or "")
    mention_id = _snowflake_from_name(raw_name)
    if mention_id:
        fetched = _fetch_user_name(mention_id)
        if fetched:
            return fetched
    return _display_name_from_text(raw_name)


def _channel_display_name(guild_id: int, channel_id: int, stored_name: Optional[str] = None) -> str:
    if stored_name:
        return f"#{stored_name}"
    fetched = _fetch_channel_name(guild_id, channel_id)
    return f"#{fetched}" if fetched else f"Channel {channel_id}"


def _channel_id_from_text(guild_id: int, value: str) -> tuple[Optional[int], Optional[str]]:
    text = (value or "").strip()
    mention = re.fullmatch(r"<#(\d+)>", text)
    if mention:
        channel_id = int(mention.group(1))
        return channel_id, _fetch_channel_name(guild_id, channel_id) or None
    if text.isdigit():
        channel_id = int(text)
        return channel_id, _fetch_channel_name(guild_id, channel_id) or None

    name = text[1:] if text.startswith("#") else text
    name = name.strip().lower()
    if not name:
        return None, None
    payload = _discord_bot_request(f"/guilds/{guild_id}/channels")
    if not isinstance(payload, list):
        return None, None
    for channel in payload:
        if not isinstance(channel, dict):
            continue
        channel_name = str(channel.get("name") or "").strip()
        if channel_name.lower() == name:
            channel_id = int(str(channel["id"]))
            CHANNEL_NAME_CACHE[channel_id] = channel_name
            DB.update_analyst_channel_name(guild_id, channel_id, channel_name)
            return channel_id, channel_name
    return None, None


def _exchange_code(code: str) -> dict[str, object]:
    payload = urlencode(
        {
            "client_id": DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _oauth_redirect_uri(),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{DISCORD_API_BASE}/oauth2/token",
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "SignalFlowDashboard/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _discord_avatar_url(user: dict[str, object]) -> str:
    avatar = str(user.get("avatar") or "")
    user_id = str(user.get("id") or "")
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png?size=80" if avatar and user_id else ""


def _is_guild_manager(guild: dict[str, object]) -> bool:
    if guild.get("owner"):
        return True
    try:
        permissions = int(str(guild.get("permissions", "0")))
    except ValueError:
        return False
    return bool(permissions & ADMINISTRATOR or permissions & MANAGE_GUILD)


def _allowed_guilds_for_discord_user(user_id: int, discord_guilds: list[dict[str, object]]) -> tuple[int, ...]:
    configured = set(_all_guild_ids())
    if user_id in OWNER_IDS:
        allowed = configured
    else:
        allowed = set()
        for guild in discord_guilds:
            guild_id = str(guild.get("id", ""))
            if guild_id.isdigit() and int(guild_id) in configured and _is_guild_manager(guild):
                allowed.add(int(guild_id))

    for guild in discord_guilds:
        guild_id = str(guild.get("id", ""))
        if not guild_id.isdigit() or int(guild_id) not in allowed:
            continue
        numeric_id = int(guild_id)
        name = str(guild.get("name") or "").strip()
        icon_url = _discord_guild_icon_url(numeric_id, guild.get("icon"))
        if name:
            GUILD_NAME_CACHE[numeric_id] = name
            DB.update_guild_metadata(numeric_id, name, icon_url)
    return tuple(sorted(allowed))


def _create_session(user: dict[str, object], discord_guilds: list[dict[str, object]]) -> str:
    user_id = int(str(user["id"]))
    username = str(user.get("global_name") or user.get("username") or f"User {user_id}")
    avatar_url = _discord_avatar_url(user)
    allowed = _allowed_guilds_for_discord_user(user_id, discord_guilds)
    now = time.time()
    token = SIGNED_SESSION_PREFIX + _sign_json(
        {
            "sid": secrets.token_urlsafe(12),
            "uid": user_id,
            "username": username,
            "avatar_url": avatar_url,
            "allowed": list(allowed),
            "iat": int(now),
            "exp": int(now + SESSION_SECONDS),
        }
    )
    with DB.connect() as conn:
        conn.execute("DELETE FROM dashboard_sessions WHERE expires_at < ?", (now,))
        conn.execute(
            """
            INSERT INTO dashboard_sessions (session_token, user_id, username, avatar_url, allowed_guild_ids, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (token, user_id, username, avatar_url, json.dumps(list(allowed)), now, now + SESSION_SECONDS),
        )
    print(
        "SignalFlow dashboard OAuth login "
        f"user_id={user_id} allowed_guilds={len(allowed)} configured_guilds={len(_all_guild_ids())} "
        f"secure_cookie={_secure_cookie()}",
        flush=True,
    )
    return token


def _load_session(token: str) -> Optional[AuthContext]:
    if not token:
        return None
    if token.startswith(SIGNED_SESSION_PREFIX):
        payload = _verify_signed_json(token.removeprefix(SIGNED_SESSION_PREFIX))
        if not payload or int(payload.get("exp", 0) or 0) < int(time.time()):
            return None
        try:
            user_id = int(payload["uid"])
            allowed = tuple(int(item) for item in payload.get("allowed", []))
        except (TypeError, ValueError, KeyError):
            return None
        if user_id in OWNER_IDS:
            allowed = _all_guild_ids()
        return AuthContext(
            ok=True,
            mode="oauth",
            user_id=user_id,
            username=str(payload.get("username") or f"User {user_id}"),
            avatar_url=str(payload.get("avatar_url") or ""),
            allowed_guild_ids=allowed,
        )
    now = time.time()
    with DB.connect() as conn:
        row = conn.execute("SELECT * FROM dashboard_sessions WHERE session_token = ?", (token,)).fetchone()
        if not row:
            return None
        if float(row["expires_at"]) < now:
            conn.execute("DELETE FROM dashboard_sessions WHERE session_token = ?", (token,))
            return None
    try:
        allowed = tuple(int(item) for item in json.loads(row["allowed_guild_ids"] or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        allowed = ()
    if int(row["user_id"]) in OWNER_IDS:
        allowed = _all_guild_ids()
    return AuthContext(
        ok=True,
        mode="oauth",
        user_id=int(row["user_id"]),
        username=str(row["username"]),
        avatar_url=str(row["avatar_url"] or ""),
        allowed_guild_ids=allowed,
    )


def _destroy_session(token: str) -> None:
    if not token:
        return
    with DB.connect() as conn:
        conn.execute("DELETE FROM dashboard_sessions WHERE session_token = ?", (token,))


def _render_css() -> str:
    return """
    :root {
      color-scheme: dark;
      --bg: #191b20;
      --panel: #24272e;
      --panel-soft: #2b2f38;
      --panel-strong: #303540;
      --line: #383f4b;
      --line-soft: rgba(255,255,255,.07);
      --text: #eef1f7;
      --muted: #a7acb7;
      --quiet: #7b8190;
      --blue: #adc1f2;
      --green: #69e8bd;
      --red: #ff8f9c;
      --yellow: #f2c96d;
      --shadow: 0 18px 48px rgba(0,0,0,.24);
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }
    a { color: inherit; }
    .app { min-height: 100vh; display: grid; grid-template-columns: 250px minmax(0, 1fr); }
    .sidebar {
      position: sticky;
      top: 0;
      height: 100vh;
      border-right: 1px solid var(--line);
      background: #1d2026;
      padding: 22px 16px;
      display: flex;
      flex-direction: column;
      gap: 20px;
    }
    .brand { display: flex; align-items: center; gap: 12px; padding: 4px 6px 14px; border-bottom: 1px solid var(--line-soft); }
    .brand-mark {
      width: 38px;
      height: 38px;
      border-radius: 8px;
      display: grid;
      place-items: center;
      background: #303744;
      color: var(--green);
      font-weight: 900;
    }
    .brand strong, h1, h2, h3, .value, button { letter-spacing: 0; }
    .brand span { display: block; color: var(--muted); font-size: 12px; margin-top: 2px; }
    .nav-group { display: grid; gap: 6px; }
    .nav-label { color: var(--quiet); font-size: 11px; font-weight: 800; text-transform: uppercase; padding: 0 8px; margin: 4px 0; }
    .nav-group a {
      text-decoration: none;
      color: var(--muted);
      border-radius: 6px;
      padding: 9px 10px;
      display: block;
    }
    .nav-group a:hover { color: var(--text); background: var(--panel); }
    .sidebar-foot { margin-top: auto; color: var(--quiet); font-size: 12px; line-height: 1.5; padding: 0 6px; }
    .main { min-width: 0; }
    .topbar {
      min-height: 76px;
      border-bottom: 1px solid var(--line);
      background: rgba(31,34,40,.88);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 18px clamp(18px, 3vw, 36px);
      position: sticky;
      top: 0;
      z-index: 3;
      backdrop-filter: blur(10px);
    }
    .topbar h1 { margin: 0; font-size: 23px; line-height: 1.1; }
    .subhead { margin-top: 6px; color: var(--muted); font-size: 13px; }
    .auth-chip { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 13px; white-space: nowrap; }
    .auth-chip img { width: 30px; height: 30px; border-radius: 999px; }
    .auth-chip a { color: var(--green); text-decoration: none; }
    main { padding: 24px clamp(18px, 3vw, 36px) 56px; max-width: 1500px; }
    .notice {
      background: rgba(105,232,189,.1);
      border: 1px solid rgba(105,232,189,.32);
      color: var(--green);
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 18px;
    }
    .hero {
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 18px;
    }
    .hero h2 { margin: 0; font-size: 18px; color: var(--blue); text-transform: uppercase; }
    .status-pill {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 999px;
      padding: 7px 11px;
      color: var(--muted);
      font-size: 13px;
    }
    .status-pill strong { color: var(--green); }
    .kpis { display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); gap: 12px; margin-bottom: 18px; }
    .kpi, .panel, .table-wrap {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .kpi { padding: 16px; min-height: 92px; }
    .label { color: var(--blue); font-size: 11px; font-weight: 850; text-transform: uppercase; }
    .value { margin-top: 10px; font-size: 25px; font-weight: 850; }
    .panel { padding: 18px; }
    .panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
    .panel h2 { margin: 0; font-size: 17px; }
    .panel p { color: var(--muted); margin: 6px 0 0; line-height: 1.45; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px; }
    .span-12 { grid-column: span 12; }
    .span-8 { grid-column: span 8; }
    .span-7 { grid-column: span 7; }
    .span-6 { grid-column: span 6; }
    .span-5 { grid-column: span 5; }
    .span-4 { grid-column: span 4; }
    section + section { margin-top: 18px; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; min-width: 860px; }
    th, td { padding: 13px 14px; border-bottom: 1px solid var(--line-soft); text-align: left; vertical-align: top; font-size: 14px; }
    th { color: var(--blue); font-size: 11px; text-transform: uppercase; background: var(--panel-soft); }
    tr:last-child td { border-bottom: 0; }
    .right { text-align: right; }
    input, select, textarea {
      width: 100%;
      background: #1d2026;
      border: 1px solid var(--line);
      border-radius: 6px;
      color: var(--text);
      padding: 10px 11px;
      font: inherit;
    }
    textarea { min-height: 120px; resize: vertical; }
    label { display: block; color: var(--blue); font-size: 11px; font-weight: 850; text-transform: uppercase; margin: 12px 0 6px; }
    button, .button {
      border: 0;
      background: var(--green);
      color: #101419;
      border-radius: 6px;
      padding: 10px 13px;
      font-weight: 850;
      cursor: pointer;
      margin-top: 12px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
    }
    button.secondary, .button.secondary { background: var(--blue); color: #12151c; }
    button.warning { background: var(--yellow); }
    button.danger { background: var(--red); }
    button.ghost, .button.ghost { background: transparent; border: 1px solid var(--line); color: var(--text); }
    .danger-text { color: var(--red) !important; }
    .inline-form { display: inline; }
    .inline-form button { margin: 0; padding: 7px 9px; }
    .pill, .tag {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      border-radius: 6px;
      border: 1px solid var(--line);
      color: var(--muted);
      background: rgba(255,255,255,.02);
      white-space: nowrap;
    }
    .pill { margin: 8px 6px 0 0; padding: 5px 8px; }
    .tag { padding: 4px 7px; font-size: 12px; text-transform: capitalize; }
    .pill strong { color: var(--text); }
    .pill em { color: var(--green); font-style: normal; }
    .muted, .sub { color: var(--muted); }
    .quiet { color: var(--quiet); }
    .good, .green { color: var(--green); }
    .bad, .red { color: var(--red); }
    .yellow { color: var(--yellow); }
    .empty { color: var(--muted); text-align: center; padding: 28px; }
    code { color: var(--blue); }
    .clean-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
    .clean-list li {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      border: 1px solid var(--line-soft);
      background: rgba(255,255,255,.02);
      border-radius: 6px;
      padding: 10px 11px;
    }
    .clean-list span { color: var(--muted); }
    .empty-list { color: var(--muted); justify-content: center !important; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 12px; }
    .divider { height: 1px; background: var(--line-soft); margin: 16px 0; }
    .login-wrap, .picker-wrap { min-height: 100vh; padding: 40px clamp(18px, 5vw, 64px); display: grid; place-items: center; }
    .login-card, .picker-card { width: min(880px, 100%); background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 28px; box-shadow: var(--shadow); }
    .login-card h1, .picker-card h1 { margin: 0; font-size: 30px; }
    .login-card p, .picker-card p { color: var(--muted); line-height: 1.55; }
    .server-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-top: 18px; }
    .server-card { border: 1px solid var(--line); border-radius: 8px; background: var(--panel-soft); padding: 16px; text-decoration: none; }
    .server-card strong { display: block; margin-bottom: 8px; }
    .server-card span { color: var(--muted); font-size: 13px; }
    @media (max-width: 1050px) {
      .app { grid-template-columns: 1fr; }
      .sidebar { position: static; height: auto; }
      .topbar { position: static; align-items: flex-start; flex-direction: column; }
      .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .span-8, .span-7, .span-6, .span-5, .span-4 { grid-column: span 12; }
    }
    @media (max-width: 650px) {
      .kpis, .form-grid { grid-template-columns: 1fr; }
      main { padding-inline: 14px; }
      .hero { align-items: flex-start; flex-direction: column; }
    }
    """


def _auth_chip(auth: AuthContext) -> str:
    if auth.is_oauth:
        avatar = f'<img src="{html.escape(auth.avatar_url)}" alt="">' if auth.avatar_url else ""
        return f'<div class="auth-chip">{avatar}<span>{html.escape(auth.username)}</span><a href="/logout">Log out</a></div>'
    if auth.is_owner_token:
        return '<div class="auth-chip"><span>Owner token access</span><a href="/login">Discord login</a></div>'
    if auth.mode == "server_token":
        return '<div class="auth-chip"><span>Legacy access</span><a href="/login">Discord login</a></div>'
    return '<div class="auth-chip"><span>Local dashboard</span><a href="/login">Discord login</a></div>'


def _is_owner_auth(auth: AuthContext) -> bool:
    if auth.is_owner_token:
        return True
    if auth.user_id and auth.user_id in OWNER_IDS:
        return True
    return False


def _owner_url(token: str = "", message: str = "", edit_guild_id: Optional[int] = None) -> str:
    params = {}
    if token:
        params["token"] = token
    if message:
        params["message"] = message
    if edit_guild_id:
        params["edit_guild_id"] = str(edit_guild_id)
    return "/owner" + (f"?{urlencode(params)}" if params else "")


def _monthly(value: object) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "$0"
    if amount <= 0:
        return "$0"
    return f"${amount:,.0f}" if amount.is_integer() else f"${amount:,.2f}"


def _billing_badge(status: object) -> str:
    value = str(status or "not_set").strip().lower()
    label = {
        "trial": "Trial",
        "active": "Active",
        "past_due": "Past Due",
        "canceled": "Canceled",
        "paused": "Paused",
        "not_set": "Not Set",
    }.get(value, value.replace("_", " ").title())
    cls = "good" if value == "active" else "yellow" if value in {"trial", "past_due"} else "bad" if value == "canceled" else "muted"
    return f'<span class="{cls}">{html.escape(label)}</span>'


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>{_render_css()}</style>
</head>
<body>{body}</body>
</html>"""


def _render_login(message: str = "", next_path: str = "/") -> str:
    configured = _oauth_configured()
    login_url = "/login/start?" + urlencode({"next": _sanitize_next(next_path)})
    config_lines = []
    if not DISCORD_CLIENT_ID:
        config_lines.append("DISCORD_CLIENT_ID")
    if not DISCORD_CLIENT_SECRET:
        config_lines.append("DISCORD_CLIENT_SECRET")
    if not _oauth_redirect_uri():
        config_lines.append("DISCORD_OAUTH_REDIRECT_URI")
    warning = ""
    if not configured:
        warning = (
            '<div class="notice" style="color:var(--yellow);border-color:rgba(242,201,109,.4);background:rgba(242,201,109,.09)">'
            f'OAuth is not fully configured yet. Missing: {html.escape(", ".join(config_lines) or "Discord OAuth app settings")}.</div>'
        )
    body = f"""
    <div class="login-wrap">
      <div class="login-card">
        <div class="brand" style="border-bottom:0;padding:0 0 18px">
          <div class="brand-mark">SF</div>
          <div><strong>SignalFlow</strong><span>Server owner control panel</span></div>
        </div>
        {f'<div class="notice">{html.escape(message)}</div>' if message else ''}
        {warning}
        <h1>Manage every server from one clean dashboard.</h1>
        <p>Sign in with Discord to see only servers where you can manage SignalFlow.</p>
        <a class="button" href="{html.escape(login_url)}">Continue with Discord</a>
        <div class="divider"></div>
        <p class="quiet">OAuth scopes used: identify and guilds. SignalFlow does not ask for brokerage access and does not place trades.</p>
      </div>
    </div>
    """
    return _page("SignalFlow Login", body)


def _render_server_picker(auth: AuthContext, token: str = "", message: str = "") -> str:
    rows = _configured_guild_rows(auth.allowed_guild_ids if auth.mode == "oauth" else None)
    cards = []
    for row in rows:
        guild_id = int(row["guild_id"])
        stats = build_analyst_stats(DATABASE_PATH, guild_id=guild_id)
        summary = _server_summary(stats)
        href = _action_url(guild_id, token)
        cards.append(
            f"""
            <a class="server-card" href="{html.escape(href)}">
              <strong>{html.escape(_guild_name(guild_id, row))}</strong>
              <span>{summary["closed"]} closed trades - {summary["open_trades"]} open - {format_pct(summary["win_rate"])} win rate</span>
            </a>
            """
        )
    if not cards:
        cards.append('<div class="server-card"><strong>No servers available</strong><span>Invite the bot or ask a server owner to grant access.</span></div>')
    body = f"""
    <div class="picker-wrap">
      <div class="picker-card">
        <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px">
          <div>
            <h1>Choose a server</h1>
            <p>Each server has its own analysts, examples, memory, branding, and stats.</p>
          </div>
          {_auth_chip(auth)}
        </div>
        {f'<div class="notice">{html.escape(message)}</div>' if message else ''}
        {f'<a class="button secondary" href="{html.escape(_owner_url(token))}">Owner Dashboard</a>' if _is_owner_auth(auth) else ''}
        <div class="server-grid">{"".join(cards)}</div>
      </div>
    </div>
    """
    return _page("SignalFlow Servers", body)


def _render_shell(guild_id: int, settings: sqlite3.Row, auth: AuthContext, token: str, message: str, content: str) -> str:
    guild_active = bool(settings["is_active"])
    status = "Active" if guild_active else "Disabled"
    body = f"""
    <div class="app">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">SF</div>
          <div><strong>SignalFlow</strong><span>Control Panel</span></div>
        </div>
        <nav class="nav-group">
          <div class="nav-label">Server</div>
          <a href="#overview">Overview</a>
          <a href="#analytics">Analytics</a>
          <a href="#setup">Setup</a>
          <a href="#examples">Examples</a>
          <a href="#memory">Memory</a>
          <a href="#settings">Settings</a>
        </nav>
        <nav class="nav-group">
          <div class="nav-label">Account</div>
          <a href="{html.escape(_action_url(None, token))}">Switch server</a>
          {f'<a href="{html.escape(_owner_url(token))}">Owner dashboard</a>' if _is_owner_auth(auth) else ''}
          <a href="{html.escape(_api_url(guild_id, token))}">Stats API</a>
          <a href="/login">Discord login</a>
        </nav>
        <div class="sidebar-foot">Alert routing only. No brokerage connections, no auto-trading, no profit promises.</div>
      </aside>
      <div class="main">
        <header class="topbar">
          <div>
            <h1>{html.escape(_guild_name(guild_id, settings))}</h1>
            <div class="subhead">Analyst routing, examples, memory, branding, and performance.</div>
          </div>
          {_auth_chip(auth)}
        </header>
        <main>
          {f'<div class="notice">{html.escape(message)}</div>' if message else ''}
          <div class="hero" id="overview">
            <div>
              <h2>Server Command Center</h2>
              <div class="subhead">All changes save directly to this server's SignalFlow database.</div>
            </div>
            <div class="status-pill">Bot status: <strong>{status}</strong></div>
          </div>
          {content}
        </main>
      </div>
    </div>
    """
    return _page(f"{_guild_name(guild_id, settings)} - SignalFlow", body)


def _owner_summary(rows: list[sqlite3.Row]) -> dict[str, object]:
    active_servers = [row for row in rows if bool(row["is_active"])]
    paying = [row for row in rows if str(row["billing_status"] or "").lower() == "active"]
    trials = [row for row in rows if str(row["billing_status"] or "").lower() == "trial"]
    past_due = [row for row in rows if str(row["billing_status"] or "").lower() == "past_due"]
    mrr = sum(float(row["monthly_price"] or 0) for row in paying)
    return {
        "servers": len(rows),
        "active_servers": len(active_servers),
        "paying": len(paying),
        "trials": len(trials),
        "past_due": len(past_due),
        "mrr": mrr,
    }


def _owner_status_select(active: bool) -> str:
    return (
        f'<option value="1"{" selected" if active else ""}>Active</option>'
        f'<option value="0"{"" if active else " selected"}>Disabled</option>'
    )


def _billing_status_select(status: object) -> str:
    selected_status = str(status or "").lower()
    options = [
        ("", "Not Set"),
        ("trial", "Trial"),
        ("active", "Active"),
        ("past_due", "Past Due"),
        ("paused", "Paused"),
        ("canceled", "Canceled"),
    ]
    return "".join(
        f'<option value="{html.escape(value)}"{" selected" if selected_status == value else ""}>{html.escape(label)}</option>'
        for value, label in options
    )


def _owner_server_options(rows: list[sqlite3.Row], selected_id: Optional[int]) -> str:
    if not rows:
        return '<option value="">No servers found</option>'
    options = []
    for row in rows:
        guild_id = int(row["guild_id"])
        selected = " selected" if selected_id == guild_id else ""
        options.append(f'<option value="{guild_id}"{selected}>{html.escape(_guild_name(guild_id, row))}</option>')
    return "".join(options)


def _owner_server_rows(rows: list[sqlite3.Row], token: str) -> str:
    table_rows = []
    for row in rows:
        guild_id = int(row["guild_id"])
        name = _guild_name(guild_id, row)
        status = "Active" if bool(row["is_active"]) else "Disabled"
        status_class = "good" if bool(row["is_active"]) else "bad"
        open_link = _action_url(guild_id, token)
        edit_link = _owner_url(token, edit_guild_id=guild_id)
        customer = str(row["customer_name"] or row["customer_email"] or row["customer_discord"] or "").strip()
        table_rows.append(
            f"""
            <tr>
              <td><strong>{html.escape(name)}</strong><div class="sub"><code>{guild_id}</code></div></td>
              <td><span class="{status_class}">{status}</span><div class="sub">{html.escape(str(row["disabled_reason"] or ""))}</div></td>
              <td>{_billing_badge(row["billing_status"])}<div class="sub">{html.escape(str(row["plan_name"] or ""))}</div></td>
              <td>{_monthly(row["monthly_price"])}</td>
              <td>{html.escape(str(row["current_period_end"] or ""))}</td>
              <td>{html.escape(customer or "No contact")}</td>
              <td>{int(row["analyst_count"] or 0)} analysts<br><span class="sub">{int(row["mapped_channel_count"] or 0)} channels / {int(row["example_count"] or 0)} examples</span></td>
              <td class="right"><a class="button ghost" href="{html.escape(open_link)}">Open</a> <a class="button secondary" href="{html.escape(edit_link)}">Edit</a></td>
            </tr>
            """
        )
    return "\n".join(table_rows) if table_rows else '<tr><td colspan="8" class="empty">No servers have created dashboard records yet.</td></tr>'


def _render_owner_dashboard(auth: AuthContext, token: str = "", message: str = "", edit_guild_id: Optional[int] = None) -> str:
    rows = DB.list_owner_guilds()
    summary = _owner_summary(rows)
    edit_row = None
    if edit_guild_id:
        edit_row = next((row for row in rows if int(row["guild_id"]) == edit_guild_id), None)
    if not edit_row and rows:
        edit_row = rows[0]
    selected_id = int(edit_row["guild_id"]) if edit_row else None
    hidden = _hidden_inputs(selected_id, token)
    action = _owner_url(token)

    edit_panel = ""
    if edit_row:
        guild_id = int(edit_row["guild_id"])
        monthly_value = "" if edit_row["monthly_price"] is None else str(edit_row["monthly_price"])
        edit_panel = f"""
        <section class="grid" id="edit-server">
          <div class="panel span-12">
            <div class="panel-head">
              <div>
                <h2>Edit Customer / Server</h2>
                <p>Phase 1 billing is manual. Use this to track who is paying and whether the bot should route alerts for that server.</p>
              </div>
              <a class="button ghost" href="{html.escape(_action_url(guild_id, token))}">Open Server Dashboard</a>
            </div>
            <form method="post" action="{html.escape(action)}" class="form-grid">
              {hidden}
              <input type="hidden" name="action" value="owner_update_guild">
              <div><label>Server</label><select name="owner_guild_id">{_owner_server_options(rows, guild_id)}</select></div>
              <div><label>Routing Status</label><select name="is_active">{_owner_status_select(bool(edit_row["is_active"]))}</select></div>
              <div><label>Customer Name</label><input name="customer_name" value="{html.escape(str(edit_row["customer_name"] or ""))}" placeholder="Connor / Evenstar Trading"></div>
              <div><label>Customer Email</label><input name="customer_email" value="{html.escape(str(edit_row["customer_email"] or ""))}" placeholder="owner@example.com"></div>
              <div><label>Discord Contact</label><input name="customer_discord" value="{html.escape(str(edit_row["customer_discord"] or ""))}" placeholder="@username or Discord ID"></div>
              <div><label>Plan Name</label><input name="plan_name" value="{html.escape(str(edit_row["plan_name"] or ""))}" placeholder="Premium Monthly"></div>
              <div><label>Monthly Price</label><input name="monthly_price" value="{html.escape(monthly_value)}" placeholder="300"></div>
              <div><label>Billing Status</label><select name="billing_status">{_billing_status_select(edit_row["billing_status"])}</select></div>
              <div><label>Current Period End</label><input name="current_period_end" value="{html.escape(str(edit_row["current_period_end"] or ""))}" placeholder="2026-06-25"></div>
              <div><label>Disabled Reason</label><input name="disabled_reason" value="{html.escape(str(edit_row["disabled_reason"] or ""))}" placeholder="Non-payment, paused, etc."></div>
              <div style="grid-column:1/-1"><label>Internal Notes</label><textarea name="billing_notes" placeholder="Setup notes, payment link, customer preferences">{html.escape(str(edit_row["billing_notes"] or ""))}</textarea></div>
              <div><button type="submit">Save Owner Settings</button></div>
            </form>
          </div>
        </section>
        """

    body = f"""
    <div class="app">
      <aside class="sidebar">
        <div class="brand">
          <div class="brand-mark">SF</div>
          <div><strong>SignalFlow</strong><span>Owner Console</span></div>
        </div>
        <nav class="nav-group">
          <div class="nav-label">Owner</div>
          <a href="#overview">Overview</a>
          <a href="#servers">Servers</a>
          <a href="#edit-server">Billing</a>
        </nav>
        <nav class="nav-group">
          <div class="nav-label">Account</div>
          <a href="{html.escape(_action_url(None, token))}">Server picker</a>
          <a href="/logout">Log out</a>
        </nav>
        <div class="sidebar-foot">Private operator dashboard. Keep billing notes and access controls away from customer admins.</div>
      </aside>
      <div class="main">
        <header class="topbar">
          <div>
            <h1>SignalFlow Owner Dashboard</h1>
            <div class="subhead">Manual billing, server access, setup status, and customer notes.</div>
          </div>
          {_auth_chip(auth)}
        </header>
        <main>
          {f'<div class="notice">{html.escape(message)}</div>' if message else ''}
          <div class="hero" id="overview">
            <div>
              <h2>Operator Overview</h2>
              <div class="subhead">Phase 1 billing: track customers manually and enable or disable server access.</div>
            </div>
          </div>
          <section>
            <div class="kpis">
              <div class="kpi"><div class="label">Servers</div><div class="value">{summary["servers"]}</div></div>
              <div class="kpi"><div class="label">Routing Active</div><div class="value">{summary["active_servers"]}</div></div>
              <div class="kpi"><div class="label">Paying</div><div class="value">{summary["paying"]}</div></div>
              <div class="kpi"><div class="label">Trial / Past Due</div><div class="value"><span class="yellow">{summary["trials"]}</span> / <span class="red">{summary["past_due"]}</span></div></div>
              <div class="kpi"><div class="label">Tracked MRR</div><div class="value">{_monthly(summary["mrr"])}</div></div>
            </div>
          </section>
          <section id="servers">
            <div class="panel-head">
              <div><h2>Customer Servers</h2><p>All configured guilds. Billing here is manual until Stripe/Whop webhooks are added.</p></div>
            </div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>Server</th><th>Routing</th><th>Billing</th><th>Monthly</th><th>Renews</th><th>Contact</th><th>Setup</th><th></th></tr></thead>
                <tbody>{_owner_server_rows(rows, token)}</tbody>
              </table>
            </div>
          </section>
          {edit_panel}
        </main>
      </div>
    </div>
    """
    return _page("SignalFlow Owner Dashboard", body)


def _render_dashboard(guild_id: int, auth: AuthContext, token: str = "", message: str = "") -> str:
    settings = _guild_settings(guild_id)
    if not settings:
        return _render_server_picker(auth, token, "That server is not configured yet.")

    stats = build_analyst_stats(DATABASE_PATH, guild_id=guild_id)
    _, open_trades = build_trade_records(DATABASE_PATH, guild_id=guild_id)
    summary = _server_summary(stats)
    analysts = DB.list_analysts(guild_id)
    channel_map = DB.get_channel_map(guild_id)
    review_channel_id = DB.get_review_channel_id(guild_id)
    example_counts = DB.count_classifier_examples_by_action(guild_id)
    examples = DB.list_classifier_examples(guild_id, limit=20)
    imports = DB.list_classifier_example_imports(guild_id, limit=10)
    hidden = _hidden_inputs(guild_id, token)
    action = _action_url(guild_id, token)
    display_name = settings["dashboard_display_name"] if settings and settings["dashboard_display_name"] else ""
    logo_url = settings["dashboard_logo_url"] if settings and settings["dashboard_logo_url"] else ""
    embed_color = settings["dashboard_embed_color"] if settings and settings["dashboard_embed_color"] else ""
    recap_brand = settings["recap_brand_name"] if settings and settings["recap_brand_name"] else ""
    recap_footer = settings["recap_footer"] if settings and settings["recap_footer"] else ""
    review_channel_label = (
        _channel_display_name(guild_id, int(review_channel_id), settings["review_channel_name"])
        if review_channel_id
        else "Not set"
    )

    content = f"""
    <section>
      <div class="kpis">
        <div class="kpi"><div class="label">Closed Trades</div><div class="value">{summary["closed"]}</div></div>
        <div class="kpi"><div class="label">Record</div><div class="value"><span class="green">{summary["wins"]}W</span> / <span class="red">{summary["losses"]}L</span></div></div>
        <div class="kpi"><div class="label">Win Rate</div><div class="value">{format_pct(summary["win_rate"])}</div></div>
        <div class="kpi"><div class="label">Open Trades</div><div class="value">{summary["open_trades"]}</div></div>
        <div class="kpi"><div class="label">Best Trade</div><div class="value" style="font-size:17px">{html.escape(str(summary["best_trade"]))}</div></div>
      </div>
    </section>

    <section id="analytics">
      <div class="panel-head">
        <div>
          <h2>Analyst Performance</h2>
          <p>Closed trades only. Trim-only updates are tracked, but they do not count as wins or losses.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Analyst</th><th>Record</th><th>Closed</th><th>Win Rate</th><th>Avg Return</th><th>Avg Win</th><th>Avg Loss</th><th>Stop-Out</th><th>Open</th><th>Best Trade</th></tr></thead>
          <tbody>{_render_stats_table(stats)}</tbody>
        </table>
      </div>
    </section>

    <section class="grid" id="setup">
      <div class="panel span-5">
        <div class="panel-head"><div><h2>Analysts</h2><p>Add analyst names or link them to Discord user IDs.</p></div></div>
        <ul class="clean-list">{_render_analyst_list(analysts)}</ul>
        <div class="divider"></div>
        <form method="post" action="{action}">
          {hidden}<input type="hidden" name="action" value="add_analyst">
          <label>Name</label><input name="name" placeholder="Randumb or Mr. M">
          <label>Discord User ID optional</label><input name="discord_user_id" placeholder="717487443804291122">
          <button type="submit">Add Analyst</button>
        </form>
        <form method="post" action="{action}">
          {hidden}<input type="hidden" name="action" value="remove_analyst">
          <label>Remove Analyst</label><select name="analyst_id">{_analyst_options(analysts)}</select>
          <button class="danger" type="submit">Remove Analyst</button>
        </form>
      </div>
      <div class="panel span-7">
        <div class="panel-head"><div><h2>Channel Routing</h2><p>An analyst can now have one or multiple alert channels.</p></div></div>
        <form method="post" action="{action}" class="form-grid">
          {hidden}<input type="hidden" name="action" value="map_channel">
          <div><label>Analyst</label><select name="analyst_id">{_analyst_options(analysts)}</select></div>
          <div><label>Alert Channel</label><input name="channel_id" placeholder="#alerts or channel ID"></div>
          <div><button type="submit">Map Channel</button></div>
        </form>
        <div class="divider"></div>
        <form method="post" action="{action}">
          {hidden}<input type="hidden" name="action" value="set_review_channel">
          <label>Review Channel <span class="quiet">current: {html.escape(review_channel_label)}</span></label>
          <input name="channel_id" placeholder="#review-alerts or channel ID">
          <button class="secondary" type="submit">Set Review Channel</button>
        </form>
        <div class="divider"></div>
        <div class="table-wrap" style="box-shadow:none">
          <table><thead><tr><th>Analyst</th><th>Channel</th><th></th></tr></thead><tbody>{_render_channel_rows(guild_id, channel_map, hidden, action)}</tbody></table>
        </div>
      </div>
    </section>

    <section class="grid" id="examples">
      <div class="panel span-4">
        <div class="panel-head"><div><h2>Detection Examples</h2><p>Server-specific wording that tunes the AI classifier.</p></div></div>
        <div class="kpis" style="grid-template-columns:repeat(2,minmax(0,1fr));margin-bottom:0">
          <div class="kpi"><div class="label">Entry</div><div class="value">{example_counts.get("entry", 0)}</div></div>
          <div class="kpi"><div class="label">Trim</div><div class="value">{example_counts.get("trim", 0)}</div></div>
          <div class="kpi"><div class="label">Close</div><div class="value">{example_counts.get("close", 0)}</div></div>
          <div class="kpi"><div class="label">Ignore</div><div class="value">{example_counts.get("ignore", 0)}</div></div>
        </div>
        <div class="divider"></div>
        <h3 class="label">Active Files</h3>
        <ul class="clean-list">{_render_import_rows(imports)}</ul>
      </div>
      <div class="panel span-8">
        <div class="panel-head"><div><h2>Example Library</h2><p>Add one high-quality example at a time or use the Discord import command for TXT/CSV files.</p></div></div>
        <form method="post" action="{action}">
          {hidden}<input type="hidden" name="action" value="add_example">
          <label>Action</label><select name="example_action">{''.join(f'<option value="{name}">{name.title()}</option>' for name in EXAMPLE_ACTIONS)}</select>
          <label>Example Text</label><textarea name="example_text" placeholder="Paste one real alert example here"></textarea>
          <button type="submit">Save Example</button>
        </form>
        <div class="divider"></div>
        <div class="table-wrap" style="box-shadow:none">
          <table><thead><tr><th>ID</th><th>Action</th><th>Text</th><th></th></tr></thead><tbody>{_render_example_rows(examples, hidden, action)}</tbody></table>
        </div>
      </div>
    </section>

    <section class="grid" id="memory">
      <div class="panel span-4">
        <div class="panel-head"><div><h2>Trade Memory</h2><p>Clear an analyst's open memory or manually close one tracked trade.</p></div></div>
        <form method="post" action="{action}">
          {hidden}<input type="hidden" name="action" value="clear_positions">
          <label>Clear Analyst Memory</label><select name="analyst_id">{_analyst_options(analysts)}</select>
          <button class="danger" type="submit">Clear Memory</button>
        </form>
        <div class="divider"></div>
        <form method="post" action="{action}">
          {hidden}<input type="hidden" name="action" value="close_position">
          <label>Analyst</label><select name="analyst_id">{_analyst_options(analysts)}</select>
          <label>Ticker</label><input name="ticker" placeholder="SPY">
          <label>Contract optional</label><input name="contract" placeholder="530C">
          <button class="danger" type="submit">Close Matching Trade</button>
        </form>
      </div>
      <div class="panel span-8">
        <div class="panel-head"><div><h2>Open Analyst Trades</h2><p>Current backend memory used for trims, stops, and contextual updates.</p></div></div>
        <div class="table-wrap" style="box-shadow:none">
          <table><thead><tr><th>Analyst</th><th>Trade</th><th>Type</th><th>Entry</th><th>Note</th><th>Opened</th></tr></thead><tbody>{_render_open_trades_rows(open_trades)}</tbody></table>
        </div>
      </div>
    </section>

    <section class="grid" id="settings">
      <div class="panel span-12">
        <div class="panel-head"><div><h2>Branding and Recaps</h2><p>These values feed the web dashboard, Discord embeds, and daily recap card.</p></div></div>
        <form method="post" action="{action}" class="form-grid">
          {hidden}<input type="hidden" name="action" value="update_settings">
          <div><label>Dashboard Display Name</label><input name="display_name" value="{html.escape(str(display_name))}" placeholder="Evenstar Trading"></div>
          <div><label>Embed Color</label><input name="embed_color" value="{html.escape(str(embed_color))}" placeholder="#2F80ED"></div>
          <div class="span-12" style="grid-column:1/-1"><label>Logo URL</label><input name="logo_url" value="{html.escape(str(logo_url))}" placeholder="https://..."></div>
          <div><label>Recap Brand Name</label><input name="recap_brand_name" value="{html.escape(str(recap_brand))}" placeholder="Evenstar Trading"></div>
          <div><label>Recap Footer</label><input name="recap_footer" value="{html.escape(str(recap_footer))}" placeholder="Evenstar Trading | Premium Recap"></div>
          <div><button type="submit">Save Settings</button></div>
        </form>
      </div>
    </section>
    """
    return _render_shell(guild_id, settings, auth, token, message, content)


class DashboardHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str, extra_headers: Optional[dict[str, object]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                if isinstance(value, list):
                    for item in value:
                        self.send_header(key, str(item))
                else:
                    self.send_header(key, str(value))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, cookies: Optional[list[str]] = None) -> None:
        headers: dict[str, object] = {"Location": location}
        if cookies:
            headers["Set-Cookie"] = cookies
        self._send(303, b"", "text/plain; charset=utf-8", headers)

    def _guild_id_from_query(self, query: dict[str, list[str]]) -> Optional[int]:
        value = query.get("guild_id", [""])[0]
        return int(value) if value.isdigit() else None

    def _read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return parse_qs(body, keep_blank_values=True)

    def _field(self, form: dict[str, list[str]], name: str) -> str:
        return form.get(name, [""])[0].strip()

    def _auth_context(
        self,
        guild_id: Optional[int],
        query: dict[str, list[str]],
        form: Optional[dict[str, list[str]]] = None,
    ) -> AuthContext:
        tokens = {self.headers.get("Authorization", "").removeprefix("Bearer ").strip()}
        tokens.add(query.get("token", [""])[0])
        if form:
            tokens.add(form.get("token", [""])[0])
        tokens.discard("")

        if DASHBOARD_TOKEN and DASHBOARD_TOKEN in tokens:
            return AuthContext(ok=True, mode="owner_token", token=DASHBOARD_TOKEN, allowed_guild_ids=_all_guild_ids())

        if guild_id:
            settings = _guild_settings(guild_id)
            guild_token = str(settings["dashboard_token"] or "") if settings else ""
            if guild_token and guild_token in tokens:
                return AuthContext(ok=True, mode="server_token", token=guild_token, allowed_guild_ids=(guild_id,))

        session_token = _parse_cookie(self.headers.get("Cookie", ""), SESSION_COOKIE)
        session = _load_session(session_token)
        if session:
            if not guild_id or guild_id in session.allowed_guild_ids:
                return session
            return AuthContext(ok=False, mode="oauth", reason="You do not have dashboard access for this server.")
        if query.get("oauth", [""])[0] == "complete":
            if session_token:
                return AuthContext(
                    ok=False,
                    reason="Discord login succeeded, but the session cookie could not be verified. Set a stable DASHBOARD_SESSION_SECRET in Railway and redeploy.",
                )
            return AuthContext(
                ok=False,
                reason="Discord login succeeded, but your browser did not return the SignalFlow session cookie. Make sure PUBLIC_DASHBOARD_URL exactly matches the Railway HTTPS domain and cookies are allowed.",
            )

        if not DASHBOARD_TOKEN and not _oauth_configured():
            allowed = _all_guild_ids()
            if not guild_id or guild_id in allowed:
                return AuthContext(ok=True, mode="local", allowed_guild_ids=allowed)

        return AuthContext(ok=False, reason="Sign in with Discord to continue.")

    def _login_redirect(self, message: str = "") -> None:
        next_path = self.path if self.path.startswith("/") else "/"
        params = {"next": _sanitize_next(next_path)}
        if message:
            params["message"] = message
        self._redirect("/login?" + urlencode(params))

    def _handle_login_start(self, query: dict[str, list[str]]) -> None:
        if not _oauth_configured():
            self._send(200, _render_login("OAuth is not configured yet.", query.get("next", ["/"])[0]).encode("utf-8"), "text/html; charset=utf-8")
            return
        next_path = _sanitize_next(query.get("next", ["/"])[0])
        state_value = _sign_json({"nonce": secrets.token_urlsafe(24), "next": next_path, "ts": int(time.time())})
        params = {
            "client_id": DISCORD_CLIENT_ID,
            "redirect_uri": _oauth_redirect_uri(),
            "response_type": "code",
            "scope": "identify guilds",
            "state": state_value,
        }
        self._redirect(
            f"https://discord.com/oauth2/authorize?{urlencode(params)}",
            cookies=[_cookie(STATE_COOKIE, state_value, STATE_SECONDS)],
        )

    def _handle_oauth_callback(self, query: dict[str, list[str]]) -> None:
        if query.get("error"):
            self._send(200, _render_login(f"Discord login was cancelled: {query.get('error_description', query['error'])[0]}").encode("utf-8"), "text/html; charset=utf-8")
            return
        state = query.get("state", [""])[0]
        state_payload = _verify_signed_json(state)
        state_cookie = _parse_cookie(self.headers.get("Cookie", ""), STATE_COOKIE)
        if not state_payload and state_cookie and hmac.compare_digest(state_cookie, state):
            state_payload = _verify_signed_json(state_cookie)
        if not state_payload:
            self._send(400, b"Invalid OAuth state. Please try logging in again.", "text/plain; charset=utf-8")
            return
        if int(time.time()) - int(state_payload.get("ts", 0) or 0) > STATE_SECONDS:
            self._send(400, b"OAuth login expired. Please try logging in again.", "text/plain; charset=utf-8")
            return
        code = query.get("code", [""])[0]
        if not code:
            self._send(400, b"Missing Discord OAuth code.", "text/plain; charset=utf-8")
            return
        try:
            token_payload = _exchange_code(code)
            access_token = str(token_payload["access_token"])
            user = _discord_request("/users/@me", access_token)
            guilds = _discord_request("/users/@me/guilds", access_token)
            if not isinstance(user, dict) or not isinstance(guilds, list):
                raise ValueError("Unexpected Discord OAuth response.")
            session_token = _create_session(user, guilds)
        except (urllib.error.URLError, KeyError, ValueError) as exc:
            self._send(502, f"Discord OAuth failed: {exc}".encode("utf-8"), "text/plain; charset=utf-8")
            return
        next_path = _sanitize_next(str(state_payload.get("next") or "/"))
        self._redirect(
            _append_query(next_path, {"oauth": "complete"}),
            cookies=[_cookie(SESSION_COOKIE, session_token, SESSION_SECONDS), _expired_cookie(STATE_COOKIE)],
        )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/login":
            self._send(200, _render_login(query.get("message", [""])[0], query.get("next", ["/"])[0]).encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/login/start":
            self._handle_login_start(query)
            return
        if parsed.path == "/oauth/callback":
            self._handle_oauth_callback(query)
            return
        if parsed.path == "/logout":
            _destroy_session(_parse_cookie(self.headers.get("Cookie", ""), SESSION_COOKIE))
            self._redirect("/login?message=Logged+out.", cookies=[_expired_cookie(SESSION_COOKIE)])
            return

        guild_id = self._guild_id_from_query(query)
        auth = self._auth_context(guild_id, query)
        if not auth.ok:
            if _oauth_configured():
                self._login_redirect(auth.reason)
            else:
                self._send(401, auth.reason.encode("utf-8"), "text/plain; charset=utf-8")
            return

        if parsed.path == "/owner":
            if not _is_owner_auth(auth):
                self._send(403, b"Owner dashboard access denied.", "text/plain; charset=utf-8")
                return
            edit_guild = query.get("edit_guild_id", [""])[0]
            edit_guild_id = int(edit_guild) if edit_guild.isdigit() else None
            body = _render_owner_dashboard(auth, auth.token, query.get("message", [""])[0], edit_guild_id).encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return

        if parsed.path == "/api/stats":
            stats = build_analyst_stats(DATABASE_PATH, guild_id=guild_id) if guild_id else []
            payload = {
                "guild_id": guild_id,
                "summary": _server_summary(stats),
                "analysts": [_stats_to_dict(item) for item in stats],
            }
            self._send(200, json.dumps(payload, indent=2).encode("utf-8"), "application/json; charset=utf-8")
            return
        if parsed.path not in {"/", "/index.html"}:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return

        if not guild_id:
            body = _render_server_picker(auth, auth.token, query.get("message", [""])[0]).encode("utf-8")
        else:
            body = _render_dashboard(guild_id, auth, auth.token, query.get("message", [""])[0]).encode("utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        form = self._read_form()
        guild_id = self._guild_id_from_query(form or query)
        auth = self._auth_context(guild_id, query, form)
        if not auth.ok:
            self._send(401, auth.reason.encode("utf-8"), "text/plain; charset=utf-8")
            return
        if parsed.path == "/owner":
            if not _is_owner_auth(auth):
                self._send(403, b"Owner dashboard access denied.", "text/plain; charset=utf-8")
                return
            message, edit_guild_id = self._handle_owner_action(form)
            self._redirect(_owner_url(auth.token, message, edit_guild_id))
            return
        if not guild_id:
            self._redirect(_action_url(None, auth.token, "No guild is available yet."))
            return

        action = self._field(form, "action")
        try:
            message = self._handle_action(guild_id, action, form)
        except Exception as exc:
            message = f"Action failed: {exc}"
        token = auth.token
        if action == "rotate_token":
            token = DB.get_or_create_dashboard_token(guild_id)
        self._redirect(_action_url(guild_id, token, message))

    def _handle_owner_action(self, form: dict[str, list[str]]) -> tuple[str, Optional[int]]:
        action = self._field(form, "action")
        guild_id_text = self._field(form, "owner_guild_id") or self._field(form, "guild_id")
        if not guild_id_text.isdigit():
            return "Choose a server first.", None
        guild_id = int(guild_id_text)

        if action != "owner_update_guild":
            return "Unknown owner action.", guild_id

        monthly_price_text = self._field(form, "monthly_price").replace("$", "").replace(",", "")
        monthly_price = None
        if monthly_price_text:
            try:
                monthly_price = float(monthly_price_text)
            except ValueError:
                return "Monthly price must be a number.", guild_id

        is_active = self._field(form, "is_active") == "1"
        DB.set_guild_active(guild_id, is_active, self._field(form, "disabled_reason"))
        DB.update_guild_billing(
            guild_id,
            customer_name=self._field(form, "customer_name"),
            customer_email=self._field(form, "customer_email"),
            customer_discord=self._field(form, "customer_discord"),
            plan_name=self._field(form, "plan_name"),
            monthly_price=monthly_price,
            billing_status=self._field(form, "billing_status"),
            current_period_end=self._field(form, "current_period_end"),
            billing_notes=self._field(form, "billing_notes"),
        )
        return "Owner billing and access settings saved.", guild_id

    def _handle_action(self, guild_id: int, action: str, form: dict[str, list[str]]) -> str:
        if action == "add_analyst":
            name = self._field(form, "name")
            user_id = self._field(form, "discord_user_id")
            if not name:
                return "Analyst name is required."
            if user_id and user_id.isdigit():
                DB.add_analyst_user(guild_id, int(user_id), name)
            else:
                DB.add_analyst(guild_id, name)
            return f"Added analyst {name}."

        if action == "remove_analyst":
            analyst_id = self._field(form, "analyst_id")
            if not analyst_id.isdigit():
                return "Choose an analyst first."
            with DB.connect() as conn:
                row = conn.execute("SELECT name FROM analysts WHERE guild_id = ? AND id = ?", (guild_id, int(analyst_id))).fetchone()
                if not row:
                    return "Analyst not found."
                conn.execute("UPDATE analysts SET is_active = 0 WHERE guild_id = ? AND id = ?", (guild_id, int(analyst_id)))
            return f"Removed analyst {row['name']}."

        if action == "map_channel":
            analyst_id = self._field(form, "analyst_id")
            channel_text = self._field(form, "channel_id")
            channel_id, channel_name = _channel_id_from_text(guild_id, channel_text)
            if not analyst_id.isdigit() or not channel_id:
                return "Analyst and alert channel are required."
            DB.set_analyst_channel(guild_id, int(analyst_id), channel_id, channel_name)
            label = f"#{channel_name}" if channel_name else f"channel {channel_id}"
            return f"Mapped {label}."

        if action == "remove_channel":
            channel_id = self._field(form, "channel_id")
            if not channel_id.isdigit():
                return "Channel ID is required."
            return "Removed channel mapping." if DB.remove_analyst_channel(guild_id, int(channel_id)) else "Channel mapping not found."

        if action == "set_review_channel":
            channel_id, channel_name = _channel_id_from_text(guild_id, self._field(form, "channel_id"))
            if not channel_id:
                return "Review channel is required."
            DB.set_review_channel(guild_id, channel_id, channel_name)
            label = f"#{channel_name}" if channel_name else f"channel {channel_id}"
            return f"Review channel updated to {label}."

        if action == "add_example":
            example_action = self._field(form, "example_action").lower()
            example_text = self._field(form, "example_text")
            if example_action not in EXAMPLE_ACTIONS:
                return "Example action must be entry, trim, close, or ignore."
            if not example_text:
                return "Example text cannot be blank."
            example_id = DB.add_classifier_example(guild_id, example_action, example_text)
            return f"Saved example #{example_id}."

        if action == "remove_example":
            example_id = self._field(form, "example_id")
            if not example_id.isdigit():
                return "Example ID is required."
            return f"Removed example #{example_id}." if DB.delete_classifier_example(guild_id, int(example_id)) else "Example not found."

        if action == "clear_positions":
            analyst_id = self._field(form, "analyst_id")
            if not analyst_id.isdigit():
                return "Choose an analyst first."
            alert_count = DB.close_all_entry_alerts(guild_id, int(analyst_id))
            user_count = DB.close_all_user_positions(guild_id, int(analyst_id))
            return f"Cleared memory: {alert_count} analyst positions and {user_count} user positions."

        if action == "close_position":
            analyst_id = self._field(form, "analyst_id")
            ticker = self._field(form, "ticker").upper()
            contract = self._field(form, "contract").upper().replace(" ", "") or None
            if not analyst_id.isdigit() or not ticker:
                return "Analyst and ticker are required."
            count = DB.close_matching_entry_alerts(guild_id, int(analyst_id), ticker, contract)
            return f"Closed {count} matching open analyst trade(s)."

        if action == "update_settings":
            DB.update_guild_dashboard_settings(
                guild_id,
                display_name=self._field(form, "display_name"),
                logo_url=self._field(form, "logo_url"),
                embed_color=self._field(form, "embed_color"),
                recap_brand_name=self._field(form, "recap_brand_name"),
                recap_footer=self._field(form, "recap_footer"),
            )
            return "Settings saved."

        if action == "rotate_token":
            DB.rotate_dashboard_token(guild_id)
            return "Dashboard token rotated. Use the new private link shown below."

        return "Unknown action."

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((DASHBOARD_HOST, DASHBOARD_PORT), DashboardHandler)
    print(f"SignalFlow dashboard running at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    if _oauth_configured():
        print(f"Discord OAuth redirect URI: {_oauth_redirect_uri()}")
    else:
        print("Discord OAuth is not configured yet. Private links and local token access still work.")
    server.serve_forever()


def start_dashboard_background() -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer((DASHBOARD_HOST, DASHBOARD_PORT), DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, name="signalflow-dashboard", daemon=True)
    thread.start()
    print(f"SignalFlow dashboard running at http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    if _oauth_configured():
        print(f"Discord OAuth redirect URI: {_oauth_redirect_uri()}")
    else:
        print("Discord OAuth is not configured yet. Private links and local token access still work.")
    return server, thread


if __name__ == "__main__":
    main()
