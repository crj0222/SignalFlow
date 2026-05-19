import discord
from discord import app_commands
from discord.ext import commands

from database import Database
from embeds import list_embed, success_embed, warning_embed


class OwnerCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database, owner_ids: set[int]) -> None:
        self.bot = bot
        self.db = db
        self.owner_ids = owner_ids

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in self.owner_ids

    @app_commands.command(name="owner_disable_server", description="Owner only: disable SignalFlow routing for a server.")
    async def owner_disable_server(self, interaction: discord.Interaction, guild_id: str, reason: str = "Disabled") -> None:
        if not self._is_owner(interaction):
            await interaction.response.send_message(embed=warning_embed("This command is owner-only."), ephemeral=True)
            return

        self.db.set_guild_active(int(guild_id), False, reason)
        await interaction.response.send_message(embed=success_embed(f"Disabled server `{guild_id}`."), ephemeral=True)

    @app_commands.command(name="owner_enable_server", description="Owner only: enable SignalFlow routing for a server.")
    async def owner_enable_server(self, interaction: discord.Interaction, guild_id: str) -> None:
        if not self._is_owner(interaction):
            await interaction.response.send_message(embed=warning_embed("This command is owner-only."), ephemeral=True)
            return

        self.db.set_guild_active(int(guild_id), True)
        await interaction.response.send_message(embed=success_embed(f"Enabled server `{guild_id}`."), ephemeral=True)

    @app_commands.command(name="owner_list_servers", description="Owner only: list server billing/routing status.")
    async def owner_list_servers(self, interaction: discord.Interaction) -> None:
        if not self._is_owner(interaction):
            await interaction.response.send_message(embed=warning_embed("This command is owner-only."), ephemeral=True)
            return

        lines = []
        for row in self.db.list_guild_statuses():
            status = "Active" if row["is_active"] else "Disabled"
            reason = f" - {row['disabled_reason']}" if row["disabled_reason"] else ""
            lines.append(f"- `{row['guild_id']}`: {status}{reason}")

        await interaction.response.send_message(
            embed=list_embed("SignalFlow Servers", lines, "No servers have been recorded yet."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot, db: Database, owner_ids: set[int]) -> None:
    await bot.add_cog(OwnerCommands(bot, db, owner_ids))
