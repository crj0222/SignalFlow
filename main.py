import logging
from dataclasses import replace
from typing import Optional

import discord
from discord.ext import commands

from commands import admin_commands, owner_commands, user_commands
from config import load_config
from database import Database
from embeds import entry_alert_embed, exit_alert_embed, position_update_embed, review_alert_embed, roll_alert_embed
from models import Analyst, ParsedAlert
from classifier import classify_alert
from parser import parse_gain_percent
from views import EntryAlertView, ExitAlertView


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("signalflow")


class SignalFlowBot(commands.Bot):
    def __init__(
        self,
        db: Database,
        guild_id: Optional[int],
        owner_ids: set[int],
        clear_guild_commands: bool = False,
    ) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.db = db
        self.guild_id = guild_id
        self.owner_ids = owner_ids
        self.clear_guild_commands = clear_guild_commands

    async def setup_hook(self) -> None:
        await user_commands.setup(self, self.db)
        await admin_commands.setup(self, self.db)
        await owner_commands.setup(self, self.db, self.owner_ids)

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

    def _confidence_level(self, confidence: str) -> str:
        value = (confidence or "high").lower()
        return {"normal": "high", "possible": "medium"}.get(value, value if value in {"high", "medium", "low"} else "medium")

    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return
        if self.user and message.author.id == self.user.id:
            return
        if not self.db.is_guild_active(message.guild.id):
            return

        analyst = self.db.get_analyst_for_channel(message.guild.id, message.channel.id)
        if not analyst:
            return

        examples = self.db.list_classifier_examples(message.guild.id)
        parsed = await classify_alert(message.content, examples)
        if not parsed:
            return
        parsed = self._apply_analyst_rule_hooks(analyst, parsed)
        parsed = self._resolve_contextual_alert(message.guild.id, analyst.id, parsed)
        confidence = self._confidence_level(parsed.confidence)
        if confidence == "low":
            return
        log.info(
            "Parsed alert action=%s confidence=%s ticker=%s contract=%s expiration=%s price=%s",
            parsed.action,
            parsed.confidence,
            parsed.ticker,
            parsed.contract,
            parsed.expiration,
            parsed.price,
        )

        if confidence == "medium":
            await self._send_review_alert(message.guild, analyst, parsed)
            return

        routed = await self.route_alert(message.guild, analyst, parsed, message.channel.id, message.id)
        log.info("Routed %s alert from %s to %s user(s)", parsed.action, analyst.name, routed)

    async def _send_review_alert(self, guild: discord.Guild, analyst: Analyst, parsed: ParsedAlert) -> None:
        channel_id = self.db.get_review_channel_id(guild.id)
        if not channel_id:
            log.info("Skipped medium-confidence %s alert; no review channel configured", parsed.action)
            return

        channel = guild.get_channel(channel_id) or self.get_channel(channel_id)
        if not channel or not hasattr(channel, "send"):
            log.warning("Review channel %s is not available", channel_id)
            return

        try:
            await channel.send(embed=review_alert_embed(analyst, parsed, guild_name=guild.name))
        except discord.HTTPException:
            log.exception("Failed to send review alert to channel %s", channel_id)

    async def route_alert(
        self,
        guild: discord.Guild,
        analyst: Analyst,
        parsed: ParsedAlert,
        channel_id: Optional[int],
        message_id: Optional[int],
    ) -> int:
        parsed = self._resolve_contextual_alert(guild.id, analyst.id, parsed)
        alert_id = self.db.log_alert(guild.id, analyst.id, channel_id, message_id, parsed)
        if parsed.is_entry:
            return await self._route_entry_alert(guild, analyst, parsed, alert_id)
        if parsed.is_position_add:
            return await self._route_position_update_alert(guild, analyst, parsed, alert_id)
        if parsed.is_roll:
            return await self._route_roll_alert(guild, analyst, parsed, alert_id)
        if parsed.is_exit:
            return await self._route_exit_alert(guild, analyst, parsed, alert_id)
        return 0

    def _logo_url(self) -> Optional[str]:
        return self.user.display_avatar.url if self.user else None

    def _apply_analyst_rule_hooks(self, analyst: Analyst, parsed: ParsedAlert) -> ParsedAlert:
        # Hook point for analyst-specific overrides without changing the shared parser.
        # Example later: if analyst.name.lower() == "randumb": return replace(parsed, ...)
        return parsed

    def _resolve_contextual_alert(self, guild_id: int, analyst_id: int, parsed: ParsedAlert) -> ParsedAlert:
        if parsed.action in {"add", "average_down", "average_up"}:
            entry = self.db.latest_open_entry_alert(guild_id, analyst_id, parsed.ticker, parsed.contract)
            if not entry and parsed.ticker:
                entry = self.db.latest_open_entry_alert(guild_id, analyst_id, parsed.ticker, None)
            if not entry:
                return replace(parsed, confidence="medium")

            entry_price = entry["price"]
            action = parsed.action
            if parsed.price is not None and entry_price is not None:
                action = "average_down" if parsed.price < float(entry_price) else "average_up"
            elif action == "add":
                return replace(parsed, confidence="medium")

            return replace(
                parsed,
                action=action,
                ticker=parsed.ticker or entry["ticker"],
                contract=parsed.contract or entry["contract"],
                expiration=entry["expiration"] or parsed.expiration,
                confidence=self._confidence_level(parsed.confidence),
            )

        if parsed.action == "roll_option":
            entry = self.db.latest_open_entry_alert(guild_id, analyst_id, parsed.ticker, parsed.old_contract or None)
            if not entry and parsed.ticker:
                entry = self.db.latest_open_entry_alert(guild_id, analyst_id, parsed.ticker, None)
            if not entry:
                entry = self.db.latest_open_entry_alert(guild_id, analyst_id)
            if not entry:
                return replace(parsed, confidence="medium")

            return replace(
                parsed,
                ticker=parsed.ticker or entry["ticker"],
                old_contract=parsed.old_contract or entry["contract"],
                old_expiration=parsed.old_expiration or entry["expiration"],
                old_price=parsed.old_price if parsed.old_price is not None else entry["price"],
                confidence="medium" if not parsed.contract else self._confidence_level(parsed.confidence),
            )

        return parsed

    async def _route_entry_alert(self, guild: discord.Guild, analyst: Analyst, parsed: ParsedAlert, alert_id: int) -> int:
        routed = 0
        embed = entry_alert_embed(analyst, parsed, guild_name=guild.name, logo_url=self._logo_url())
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

    async def _route_position_update_alert(self, guild: discord.Guild, analyst: Analyst, parsed: ParsedAlert, alert_id: int) -> int:
        analyst_entry = self.db.latest_open_entry_alert(guild.id, analyst.id, parsed.ticker, parsed.contract)
        if not analyst_entry and parsed.ticker:
            analyst_entry = self.db.latest_open_entry_alert(guild.id, analyst.id, parsed.ticker, None)
        if not analyst_entry:
            return 0

        routed = 0
        for user_id in self.db.subscribed_users(guild.id, analyst.id):
            positions = self.db.find_open_positions_for_entry_alert(guild.id, user_id, analyst_entry["id"])
            if not positions:
                continue

            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if not user:
                continue

            position = positions[0]
            reference_price = position["average_price"] if position["average_price"] is not None else position["entry_price"]
            try:
                await user.send(
                    embed=position_update_embed(
                        analyst,
                        parsed,
                        reference_price=reference_price,
                        guild_name=guild.name,
                        logo_url=self._logo_url(),
                    ),
                    view=ExitAlertView(self.db, guild.id, position["id"], alert_id),
                )
                self.db.record_average_event(position["id"], user_id, alert_id, parsed.action, parsed.price, parsed.raw_text)
                routed += 1
            except discord.Forbidden:
                log.warning("Cannot DM user %s; DMs may be closed", user_id)
            except discord.HTTPException:
                log.exception("Failed to DM position update alert to user %s", user_id)
        return routed

    async def _route_roll_alert(self, guild: discord.Guild, analyst: Analyst, parsed: ParsedAlert, alert_id: int) -> int:
        old_entry = self.db.latest_open_entry_alert(guild.id, analyst.id, parsed.ticker, parsed.old_contract or None)
        if not old_entry and parsed.ticker:
            old_entry = self.db.latest_open_entry_alert(guild.id, analyst.id, parsed.ticker, None)
        if not old_entry:
            old_entry = self.db.latest_open_entry_alert(guild.id, analyst.id)
        if not old_entry:
            return 0

        self.db.update_roll_alert_details(
            alert_id,
            parsed.old_contract or old_entry["contract"],
            parsed.old_expiration or old_entry["expiration"],
            parsed.old_price if parsed.old_price is not None else old_entry["price"],
        )
        self.db.mark_entry_alert_rolled(old_entry["id"], alert_id)

        routed = 0
        for user_id in self.db.subscribed_users(guild.id, analyst.id):
            positions = self.db.find_open_positions_for_entry_alert(guild.id, user_id, old_entry["id"])
            if not positions:
                continue

            user = self.get_user(user_id) or await self.fetch_user(user_id)
            if not user:
                continue

            position = positions[0]
            old_price = position["average_price"] if position["average_price"] is not None else position["entry_price"]
            try:
                await user.send(
                    embed=roll_alert_embed(
                        analyst,
                        parsed,
                        old_ticker=old_entry["ticker"],
                        old_contract=parsed.old_contract or old_entry["contract"],
                        old_expiration=parsed.old_expiration or old_entry["expiration"],
                        old_price=old_price,
                        guild_name=guild.name,
                        logo_url=self._logo_url(),
                    ),
                    view=ExitAlertView(self.db, guild.id, position["id"], alert_id),
                )
                self.db.roll_position(
                    position["id"],
                    user_id,
                    alert_id,
                    parsed.ticker or old_entry["ticker"],
                    parsed.contract,
                    parsed.expiration,
                    parsed.price,
                    parsed.roll_cost,
                    parsed.roll_cost_type,
                    parsed.raw_text,
                )
                routed += 1
            except discord.Forbidden:
                log.warning("Cannot DM user %s; DMs may be closed", user_id)
            except discord.HTTPException:
                log.exception("Failed to DM roll alert to user %s", user_id)
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
        closes_analyst_trade = parsed.action in {"close", "stop", "exit"}
        if missing_trade_details or closes_analyst_trade:
            lookup_ticker = parsed.ticker if not missing_trade_details else None
            lookup_contract = parsed.contract if not missing_trade_details else None
            analyst_entry = self.db.latest_open_entry_alert(guild.id, analyst.id, lookup_ticker, lookup_contract)
            if closes_analyst_trade and not analyst_entry:
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
            if analyst_entry:
                display_parsed = self._entry_to_display_alert(parsed, analyst_entry)
            else:
                display_parsed = replace(
                    parsed,
                    ticker=parsed.ticker or position["ticker"],
                    contract=parsed.contract or position["contract"],
                    expiration=position["expiration"] if missing_trade_details else (parsed.expiration or position["expiration"]),
                    confidence="normal",
                )
            exit_actions = {"trim", "close", "stop", "exit"}
            gain_price = parsed.price if parsed.action in exit_actions else None
            basis_price = position["average_price"] if position["average_price"] is not None else position["entry_price"]
            gain_pct = self._trim_gain_pct(basis_price, gain_price) if parsed.action in exit_actions else None
            if gain_pct is None and parsed.action in exit_actions:
                gain_pct = parse_gain_percent(parsed.raw_text)
            try:
                await user.send(
                    embed=exit_alert_embed(
                        analyst,
                        display_parsed,
                        possible=False,
                        gain_pct=gain_pct,
                        guild_name=guild.name,
                        logo_url=self._logo_url(),
                    ),
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
    bot = SignalFlowBot(
        db=db,
        guild_id=config.guild_id,
        owner_ids=config.owner_ids,
        clear_guild_commands=config.clear_guild_commands,
    )
    bot.run(config.token)


if __name__ == "__main__":
    main()
