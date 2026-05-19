import logging
from dataclasses import replace
from typing import Optional

import discord
from discord.ext import commands

from commands import admin_commands, user_commands
from config import load_config
from database import Database
from embeds import entry_alert_embed, exit_alert_embed
from models import Analyst, ParsedAlert
from classifier import classify_alert
from views import EntryAlertView, ExitAlertView


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("signalflow")


class SignalFlowBot(commands.Bot):
    def __init__(self, db: Database, guild_id: Optional[int], clear_guild_commands: bool = False) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.db = db
        self.guild_id = guild_id
        self.clear_guild_commands = clear_guild_commands

    async def setup_hook(self) -> None:
        await user_commands.setup(self, self.db)
        await admin_commands.setup(self, self.db)

        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            if self.clear_guild_commands:
                self.tree.clear_commands(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Cleared %s guild slash commands from %s", len(synced), self.guild_id)
                return
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s guild slash commands to %s", len(synced), self.guild_id)
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global slash commands", len(synced))

    async def on_ready(self) -> None:
        log.info("SignalFlow is online as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if self.user and message.author.id == self.user.id:
            return

        analyst = self.db.get_analyst_for_channel(message.guild.id, message.channel.id)
        if not analyst:
            return

        parsed = await classify_alert(message.content)
        if not parsed:
            return
        log.info(
            "Parsed alert action=%s ticker=%s contract=%s expiration=%s price=%s",
            parsed.action,
            parsed.ticker,
            parsed.contract,
            parsed.expiration,
            parsed.price,
        )

        routed = await self.route_alert(message.guild, analyst, parsed, message.channel.id, message.id)
        log.info("Routed %s alert from %s to %s user(s)", parsed.action, analyst.name, routed)

    async def route_alert(
        self,
        guild: discord.Guild,
        analyst: Analyst,
        parsed: ParsedAlert,
        channel_id: Optional[int],
        message_id: Optional[int],
    ) -> int:
        alert_id = self.db.log_alert(guild.id, analyst.id, channel_id, message_id, parsed)
        if parsed.is_entry:
            return await self._route_entry_alert(guild, analyst, parsed, alert_id)
        if parsed.is_exit:
            return await self._route_exit_alert(guild, analyst, parsed, alert_id)
        return 0

    async def _route_entry_alert(self, guild: discord.Guild, analyst: Analyst, parsed: ParsedAlert, alert_id: int) -> int:
        routed = 0
        embed = entry_alert_embed(analyst, parsed)
        for user_id in self.db.subscribed_users(guild.id, analyst.id):
            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if not user:
                continue
            try:
                await user.send(embed=embed, view=EntryAlertView(self.db, guild.id, analyst.id, alert_id))
                routed += 1
            except discord.Forbidden:
                log.warning("Cannot DM user %s; DMs may be closed", user_id)
            except discord.HTTPException:
                log.exception("Failed to DM entry alert to user %s", user_id)
        return routed

    def _trim_gain_pct(self, entry_price: Optional[float], trim_price: Optional[float]) -> Optional[float]:
        if entry_price is None or trim_price is None or entry_price <= 0:
            return None
        return ((trim_price - entry_price) / entry_price) * 100

    def _entry_to_display_alert(self, parsed: ParsedAlert, entry) -> ParsedAlert:
        return replace(
            parsed,
            ticker=entry["ticker"],
            contract=entry["contract"],
            expiration=entry["expiration"],
            price=parsed.price if parsed.price is not None else entry["price"],
            trade_note=entry["trade_note"] or parsed.trade_note,
            confidence="normal",
        )

    async def _route_exit_alert(self, guild: discord.Guild, analyst: Analyst, parsed: ParsedAlert, alert_id: int) -> int:
        routed = 0
        missing_trade_details = not (parsed.ticker or parsed.contract)
        analyst_entry = None
        closes_analyst_trade = parsed.action in {"stop", "exit"}
        if closes_analyst_trade:
            lookup_ticker = parsed.ticker if not missing_trade_details else None
            lookup_contract = parsed.contract if not missing_trade_details else None
            analyst_entry = self.db.latest_open_entry_alert(guild.id, analyst.id, lookup_ticker, lookup_contract)
            if not analyst_entry:
                return 0

        for user_id in self.db.subscribed_users(guild.id, analyst.id):
            if analyst_entry:
                positions = self.db.find_open_positions_for_entry_alert(guild.id, user_id, analyst_entry["id"])
            else:
                positions = self.db.find_open_positions(guild.id, user_id, analyst.id, parsed.ticker, parsed.contract)
            if not positions:
                continue

            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if not user:
                continue

            position = positions[0]
            view = None if closes_analyst_trade else ExitAlertView(self.db, guild.id, position["id"], alert_id)
            if closes_analyst_trade and analyst_entry:
                display_parsed = self._entry_to_display_alert(parsed, analyst_entry)
            else:
                display_parsed = replace(
                    parsed,
                    ticker=parsed.ticker or position["ticker"],
                    contract=parsed.contract or position["contract"],
                    expiration=position["expiration"] if missing_trade_details else (parsed.expiration or position["expiration"]),
                    confidence="normal",
                )
            gain_pct = self._trim_gain_pct(position["entry_price"], display_parsed.price) if parsed.action == "trim" else None
            try:
                await user.send(
                    embed=exit_alert_embed(analyst, display_parsed, possible=False, gain_pct=gain_pct),
                    view=view,
                )
                if closes_analyst_trade:
                    self.db.close_position(position["id"], user_id)
                routed += 1
            except discord.Forbidden:
                log.warning("Cannot DM user %s; DMs may be closed", user_id)
            except discord.HTTPException:
                log.exception("Failed to DM trim/exit alert to user %s", user_id)
        if closes_analyst_trade and analyst_entry:
            self.db.close_entry_alert(analyst_entry["id"])
        return routed


def main() -> None:
    config = load_config()
    db = Database(config.database_path)
    bot = SignalFlowBot(db=db, guild_id=config.guild_id, clear_guild_commands=config.clear_guild_commands)
    bot.run(config.token)


if __name__ == "__main__":
    main()
