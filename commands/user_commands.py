import re

import discord
from discord import app_commands
from discord.ext import commands

from database import Database
from embeds import help_embed, list_embed, start_embed, success_embed, warning_embed
from models import Analyst
from views import AnalystSelectView, build_analyst_picker_embed


MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def _clean_name(name: str) -> str:
    value = name.strip()
    return value[1:] if value.startswith("@") else value


class UserCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database) -> None:
        self.bot = bot
        self.db = db

    async def _resolve_analyst_names(self, interaction: discord.Interaction, analysts: list[Analyst]) -> list[Analyst]:
        resolved: list[Analyst] = []
        guild = interaction.guild

        for analyst in analysts:
            user_id = analyst.discord_user_id
            mention = MENTION_RE.match(analyst.name.strip())
            if mention:
                user_id = int(mention.group(1))

            display_name = _clean_name(analyst.name)
            if user_id:
                member = guild.get_member(user_id) if guild else None
                user = member or self.bot.get_user(user_id)
                if user is None:
                    try:
                        user = await self.bot.fetch_user(user_id)
                    except discord.HTTPException:
                        user = None

                if isinstance(user, discord.Member):
                    display_name = user.display_name
                elif isinstance(user, discord.User):
                    display_name = user.global_name or user.name

            resolved.append(
                Analyst(
                    id=analyst.id,
                    guild_id=analyst.guild_id,
                    name=display_name,
                    is_active=analyst.is_active,
                    discord_user_id=user_id,
                )
            )

        return resolved

    @app_commands.command(name="start", description="Learn what SignalFlow does.")
    async def start(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=start_embed(), ephemeral=True)

    @app_commands.command(name="signalflow_help", description="Show SignalFlow commands and alert controls.")
    async def signalflow_help(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=help_embed(), ephemeral=True)

    @app_commands.command(name="select_analysts", description="Choose which analysts can DM you alerts.")
    async def select_analysts(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analysts = await self._resolve_analyst_names(interaction, self.db.list_analysts(interaction.guild_id))
        selected = await self._resolve_analyst_names(
            interaction,
            self.db.list_user_subscriptions(interaction.guild_id, interaction.user.id),
        )
        selected_ids = {analyst.id for analyst in selected}
        view = AnalystSelectView(self.db, interaction.guild_id, analysts, selected_ids)
        embed = build_analyst_picker_embed(analysts, selected_ids)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="my_alerts", description="Show which analysts you follow.")
    async def my_alerts(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analysts = await self._resolve_analyst_names(
            interaction,
            self.db.list_user_subscriptions(interaction.guild_id, interaction.user.id),
        )
        lines = [f"- {analyst.name}" for analyst in analysts]
        await interaction.response.send_message(
            embed=list_embed("My Alerts", lines, "You are not following any analysts yet. Use `/select_analysts`."),
            ephemeral=True,
        )

    @app_commands.command(name="current_positions", description="Show an analyst's currently open SignalFlow trades.")
    async def current_positions(self, interaction: discord.Interaction, analyst: discord.User) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        analyst_row = self.db.get_analyst_by_user_id(interaction.guild_id, analyst.id)
        if not analyst_row:
            names_to_try = [
                getattr(analyst, "display_name", None),
                getattr(analyst, "global_name", None),
                analyst.name,
            ]
            for name in [name for name in names_to_try if name]:
                analyst_row = self.db.get_analyst_by_name(interaction.guild_id, name)
                if analyst_row:
                    break

        if not analyst_row:
            await interaction.response.send_message(
                embed=warning_embed("That analyst is not configured in this server."),
                ephemeral=True,
            )
            return

        names_to_try = [
            analyst_row.name,
            getattr(analyst, "display_name", None),
            getattr(analyst, "global_name", None),
            analyst.name,
        ]
        positions = self.db.list_open_entry_alerts_for_analyst_user(interaction.guild_id, analyst.id, names_to_try)
        if not positions:
            positions = self.db.list_open_entry_alerts(interaction.guild_id, analyst_row.id)
        lines = []
        for pos in positions:
            trade = " ".join(part for part in [pos["ticker"], pos["expiration"], pos["contract"]] if part)
            price = f" @{pos['price']:g}" if pos["price"] is not None else ""
            note = f" - {pos['trade_note']}" if pos["trade_note"] else ""
            lines.append(f"- **{trade or 'Details not detected'}{price}**{note}")

        await interaction.response.send_message(
            embed=list_embed(
                f"{analyst_row.name} Current Positions",
                lines,
                "No open SignalFlow trades are currently tracked for this analyst.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="pause_alerts", description="Pause all SignalFlow DMs in this server.")
    async def pause_alerts(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        self.db.set_user_pause(interaction.guild_id, interaction.user.id, True)
        await interaction.response.send_message(embed=success_embed("Your SignalFlow DMs are paused."), ephemeral=True)

    @app_commands.command(name="resume_alerts", description="Resume SignalFlow DMs in this server.")
    async def resume_alerts(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        self.db.set_user_pause(interaction.guild_id, interaction.user.id, False)
        await interaction.response.send_message(embed=success_embed("Your SignalFlow DMs are resumed."), ephemeral=True)

async def setup(bot: commands.Bot, db: Database) -> None:
    await bot.add_cog(UserCommands(bot, db))
