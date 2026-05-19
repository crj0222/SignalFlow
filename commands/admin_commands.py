import discord
from discord import app_commands
from discord.ext import commands

from database import Database
from embeds import list_embed, success_embed, warning_embed
from models import ParsedAlert


def analyst_display_name(user: discord.User | discord.Member) -> str:
    if isinstance(user, discord.Member):
        return user.display_name
    return user.global_name or user.name


def analyst_label(analyst_id: int | None, name: str) -> str:
    return f"<@{analyst_id}> ({name})" if analyst_id else name


def resolve_analyst(db: Database, guild_id: int, user: discord.User | discord.Member):
    analyst = db.get_analyst_by_user_id(guild_id, user.id)
    if analyst:
        return analyst

    names_to_try = [
        getattr(user, "display_name", None),
        getattr(user, "global_name", None),
        user.name,
    ]
    for name in [name for name in names_to_try if name]:
        analyst = db.get_analyst_by_name(guild_id, name)
        if analyst:
            return analyst
    return None


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        return bool(perms and perms.manage_guild)

    return app_commands.check(predicate)


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database) -> None:
        self.bot = bot
        self.db = db

    @app_commands.command(name="admin_add_analyst", description="Add or reactivate an analyst bot/user.")
    @admin_only()
    async def admin_add_analyst(self, interaction: discord.Interaction, analyst: discord.User) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        display_name = analyst_display_name(analyst)
        self.db.add_analyst_user(interaction.guild_id, analyst.id, display_name)
        await interaction.response.send_message(
            embed=success_embed(f"Added analyst {analyst.mention} as **{display_name}**."),
            ephemeral=True,
        )

    @app_commands.command(name="admin_remove_analyst", description="Remove an analyst from active routing.")
    @admin_only()
    async def admin_remove_analyst(self, interaction: discord.Interaction, analyst: discord.User) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analyst_row = resolve_analyst(self.db, interaction.guild_id, analyst)
        removed = self.db.remove_analyst_user(interaction.guild_id, analyst.id)
        if not removed and analyst_row:
            removed = self.db.remove_analyst(interaction.guild_id, analyst_row.name)
        embed = success_embed(f"Removed {analyst.mention}.") if removed else warning_embed("I could not find that analyst.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="admin_set_channel", description="Map an analyst to a Discord channel.")
    @admin_only()
    async def admin_set_channel(self, interaction: discord.Interaction, analyst: discord.User, channel: discord.TextChannel) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analyst_row = resolve_analyst(self.db, interaction.guild_id, analyst)
        if not analyst_row:
            await interaction.response.send_message(embed=warning_embed("Add that analyst first with `/admin_add_analyst` and their @ mention."), ephemeral=True)
            return

        self.db.set_analyst_channel(interaction.guild_id, analyst_row.id, channel.id)
        await interaction.response.send_message(
            embed=success_embed(f"Mapped {analyst.mention} to {channel.mention}."),
            ephemeral=True,
        )

    @app_commands.command(name="admin_list_analysts", description="List analysts and mapped channels.")
    @admin_only()
    async def admin_list_analysts(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analysts = self.db.list_analysts(interaction.guild_id)
        channel_map = {row["name"]: row["channel_id"] for row in self.db.get_channel_map(interaction.guild_id)}
        lines = []
        for analyst in analysts:
            channel_id = channel_map.get(analyst.name)
            label = analyst_label(analyst.discord_user_id, analyst.name)
            lines.append(f"- {label} - <#{channel_id}>" if channel_id else f"- {label} - no channel")

        await interaction.response.send_message(
            embed=list_embed("Analysts", lines, "No analysts configured yet."),
            ephemeral=True,
        )

    @app_commands.command(name="admin_set_review_channel", description="Set a channel for parser review notes.")
    @admin_only()
    async def admin_set_review_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        self.db.set_review_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(embed=success_embed(f"Review channel set to {channel.mention}."), ephemeral=True)

    @app_commands.command(name="admin_clear_positions", description="Clear all tracked open positions for an analyst.")
    @admin_only()
    async def admin_clear_positions(self, interaction: discord.Interaction, analyst: discord.User) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analyst_row = resolve_analyst(self.db, interaction.guild_id, analyst)
        if not analyst_row:
            await interaction.response.send_message(embed=warning_embed("I could not find that analyst."), ephemeral=True)
            return

        count = self.db.close_all_entry_alerts(interaction.guild_id, analyst_row.id)
        await interaction.response.send_message(
            embed=success_embed(f"Cleared {count} open tracked position(s) for **{analyst_row.name}**."),
            ephemeral=True,
        )

    @app_commands.command(name="admin_close_position", description="Close one tracked analyst position by ticker and optional contract.")
    @admin_only()
    async def admin_close_position(
        self,
        interaction: discord.Interaction,
        analyst: discord.User,
        ticker: str,
        contract: str | None = None,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analyst_row = resolve_analyst(self.db, interaction.guild_id, analyst)
        if not analyst_row:
            await interaction.response.send_message(embed=warning_embed("I could not find that analyst."), ephemeral=True)
            return

        normalized_contract = contract.upper().replace(" ", "") if contract else None
        count = self.db.close_matching_entry_alerts(
            interaction.guild_id,
            analyst_row.id,
            ticker.upper(),
            normalized_contract,
        )
        await interaction.response.send_message(
            embed=success_embed(f"Closed {count} tracked position(s) for **{analyst_row.name}**."),
            ephemeral=True,
        )

    @app_commands.command(name="admin_test_alert", description="Send a fake alert through SignalFlow.")
    @admin_only()
    async def admin_test_alert(
        self,
        interaction: discord.Interaction,
        analyst: discord.User,
        action: str,
        ticker: str,
        contract: str,
        expiration: str,
        price: float,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analyst_row = resolve_analyst(self.db, interaction.guild_id, analyst)
        if not analyst_row:
            await interaction.response.send_message(embed=warning_embed("Add that analyst first with `/admin_add_analyst` and their @ mention."), ephemeral=True)
            return

        normalized_action = action.lower()
        if normalized_action not in {"entry", "trim", "exit", "stop"}:
            await interaction.response.send_message(embed=warning_embed("Action must be `entry`, `trim`, `exit`, or `stop`."), ephemeral=True)
            return

        parsed = ParsedAlert(
            action=normalized_action,
            confidence="normal",
            ticker=ticker.upper(),
            contract=contract.upper(),
            expiration=expiration,
            price=price,
            raw_text=f"TEST {action} {ticker} {contract} {expiration} @ {price}",
            trade_note="Day Trade",
        )
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild:
            await interaction.followup.send(embed=warning_embed("Use this command inside your server."), ephemeral=True)
            return
        routed = await self.bot.route_alert(interaction.guild, analyst_row, parsed, None, None)
        await interaction.followup.send(embed=success_embed(f"Test alert routed to {routed} user DM(s)."), ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(embed=warning_embed("You need Manage Server permission for admin commands."), ephemeral=True)
            return
        raise error


async def setup(bot: commands.Bot, db: Database) -> None:
    await bot.add_cog(AdminCommands(bot, db))
