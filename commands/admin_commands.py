import logging
import os
import re
from datetime import date
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from database import Database
from embeds import BRAND_COLOR, FOOTER, list_embed, success_embed, warning_embed
from example_importer import examples_from_csv_bytes, examples_from_txt_bytes
from models import ParsedAlert
from recap_renderer import build_recap_card_from_database, render_recap_card


EXAMPLE_ACTION_LABELS = {
    "entry": "Entry",
    "trim": "Trim",
    "close": "Close",
    "ignore": "Ignore",
}
log = logging.getLogger("signalflow.admin")


def analyst_display_name(user: discord.User | discord.Member) -> str:
    if isinstance(user, discord.Member):
        return user.display_name
    return user.global_name or user.name


def analyst_label(analyst_id: int | None, name: str) -> str:
    clean_name = " ".join((name or "").split()).strip()
    if clean_name.startswith("@"):
        clean_name = clean_name[1:]
    if clean_name in {str(analyst_id), f"<@{analyst_id}>", f"<@!{analyst_id}>"}:
        return f"Analyst {str(analyst_id)[-4:]}" if analyst_id else "Analyst"
    return clean_name or (f"Analyst {str(analyst_id)[-4:]}" if analyst_id else "Analyst")


def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        return bool(perms and perms.manage_guild)

    return app_commands.check(predicate)


def _example_counts_text(counts: dict[str, int]) -> str:
    total = sum(counts.get(action, 0) for action in EXAMPLE_ACTION_LABELS)
    if total == 0:
        return "No server examples imported yet."
    parts = [f"{label} `{counts.get(action, 0)}`" for action, label in EXAMPLE_ACTION_LABELS.items()]
    return "  ".join(parts) + f"\nTotal `{total}`"


def _short_example(text: str, limit: int = 92) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[: limit - 3]}..."


def _bounded_lines(lines: list[str], empty: str, max_chars: int = 1000) -> str:
    if not lines:
        return empty

    output = []
    used = 0
    for line in lines:
        extra = len(line) + (1 if output else 0)
        if used + extra > max_chars:
            remaining = len(lines) - len(output)
            if remaining > 0:
                suffix = f"...and {remaining} more."
                if used + len(suffix) + 1 <= max_chars:
                    output.append(suffix)
            break
        output.append(line)
        used += extra
    return "\n".join(output) if output else empty


def _active_imports_text(imports, counts: dict[str, int]) -> str:
    if imports:
        lines = []
        for row in imports:
            file_type = str(row["file_type"]).upper()
            filename = str(row["filename"])
            action = str(row["action"])
            if action not in EXAMPLE_ACTION_LABELS:
                continue
            saved = int(row["saved_count"] or 0)
            scanned = int(row["scanned_count"] or 0)
            lines.append(f"- `{filename}` `{file_type}` - `{action}` saved `{saved}` / scanned `{scanned}`")
        if lines:
            return _bounded_lines(lines, "No active example files tracked yet.")

    total = sum(counts.get(action, 0) for action in EXAMPLE_ACTION_LABELS)
    if total:
        active = [f"{label} `{counts.get(action, 0)}`" for action, label in EXAMPLE_ACTION_LABELS.items() if counts.get(action, 0)]
        return "Examples are active, but the source filenames were imported before file tracking existed.\n" + "  ".join(active)
    return "No active example files tracked yet."


def _snowflake(text: str) -> int | None:
    match = re.search(r"\d{15,25}", text or "")
    return int(match.group(0)) if match else None


def _clean_name(text: str) -> str:
    return (text or "").strip().strip("#").strip("@").strip()


def _normalize_action(value: str) -> str | None:
    clean = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "exit": "close",
        "stop": "close",
        "stopped": "close",
    }
    clean = aliases.get(clean, clean)
    return clean if clean in EXAMPLE_ACTION_LABELS else None


async def _resolve_user(bot: commands.Bot, guild: discord.Guild, value: str) -> discord.User | discord.Member | None:
    user_id = _snowflake(value)
    if user_id:
        member = guild.get_member(user_id)
        if member:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            pass
        try:
            return await bot.fetch_user(user_id)
        except discord.HTTPException:
            return None

    target = _clean_name(value).lower()
    if not target:
        return None
    for member in guild.members:
        if target in {member.name.lower(), member.display_name.lower(), str(member).lower()}:
            return member
    return None


def _resolve_channel(guild: discord.Guild, value: str) -> discord.TextChannel | None:
    channel_id = _snowflake(value)
    if channel_id:
        channel = guild.get_channel(channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    target = _clean_name(value).lower()
    for channel in guild.text_channels:
        if channel.name.lower() == target:
            return channel
    return None


def _resolve_analyst_text(db: Database, guild_id: int, value: str):
    user_id = _snowflake(value)
    if user_id:
        analyst = db.get_analyst_by_user_id(guild_id, user_id)
        if analyst:
            return analyst
    name = _clean_name(value)
    return db.get_analyst_by_name(guild_id, name) if name else None


async def _send_dashboard_error(interaction: discord.Interaction, message: str) -> None:
    embed = warning_embed(message)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


class DashboardModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Dashboard modal failed", exc_info=(type(error), error, error.__traceback__))
        await _send_dashboard_error(interaction, "That dashboard action failed. Check the bot console for the exact error.")


class AddAnalystModal(DashboardModal, title="Add Analyst"):
    analyst = discord.ui.TextInput(label="Analyst @, ID, or name", placeholder="@Randumb or 1234567890", max_length=100)
    display_name = discord.ui.TextInput(label="Display name override", placeholder="Optional", required=False, max_length=80)

    def __init__(self, cog: "AdminCommands", guild_id: int, guild_name: str) -> None:
        super().__init__()
        self.cog = cog
        self.guild_id = guild_id
        self.guild_name = guild_name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_add_analyst(interaction, self.analyst.value, self.display_name.value)


class RemoveAnalystModal(DashboardModal, title="Remove Analyst"):
    analyst = discord.ui.TextInput(label="Analyst @, ID, or name", placeholder="@Randumb or Randumb", max_length=100)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_remove_analyst(interaction, self.analyst.value)


class SetChannelModal(DashboardModal, title="Map Analyst Channel"):
    analyst = discord.ui.TextInput(label="Analyst @, ID, or name", placeholder="@Randumb or Randumb", max_length=100)
    channel = discord.ui.TextInput(label="Channel #, ID, or name", placeholder="#randumb-alerts", max_length=100)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_set_channel(interaction, self.analyst.value, self.channel.value)


class ReviewChannelModal(DashboardModal, title="Set Review Channel"):
    channel = discord.ui.TextInput(label="Review channel #, ID, or name", placeholder="#review-alerts", max_length=100)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_set_review_channel(interaction, self.channel.value)


class AddExampleModal(DashboardModal, title="Add Classifier Example"):
    action = discord.ui.TextInput(label="Action", placeholder="entry, trim, close, or ignore", max_length=40)
    text = discord.ui.TextInput(label="Example text", style=discord.TextStyle.paragraph, max_length=900)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_add_example(interaction, self.action.value, self.text.value)


class RemoveExampleModal(DashboardModal, title="Remove Example"):
    example_id = discord.ui.TextInput(label="Example ID", placeholder="Example number from the Examples page", max_length=20)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_remove_example(interaction, self.example_id.value)


class ClearPositionsModal(DashboardModal, title="Clear Analyst Positions"):
    analyst = discord.ui.TextInput(label="Analyst @, ID, or name", placeholder="@Randumb or Randumb", max_length=100)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_clear_positions(interaction, self.analyst.value)


class ClosePositionModal(DashboardModal, title="Close One Position"):
    analyst = discord.ui.TextInput(label="Analyst @, ID, or name", placeholder="@Randumb or Randumb", max_length=100)
    ticker = discord.ui.TextInput(label="Ticker", placeholder="SPY", max_length=12)
    contract = discord.ui.TextInput(label="Contract", placeholder="Optional, e.g. 530C", required=False, max_length=20)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_close_position(interaction, self.analyst.value, self.ticker.value, self.contract.value)


class TestAlertModal(DashboardModal, title="Send Test Alert"):
    analyst = discord.ui.TextInput(label="Analyst @, ID, or name", placeholder="@Randumb or Randumb", max_length=100)
    details = discord.ui.TextInput(label="Action ticker contract expiration price", placeholder="entry SPY 530C 5/24 1.20", max_length=120)

    def __init__(self, cog: "AdminCommands") -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.dashboard_test_alert(interaction, self.analyst.value, self.details.value)


class AdminDashboardView(discord.ui.View):
    def __init__(self, cog: "AdminCommands", guild_id: int, guild_name: str, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Open your own admin dashboard with `/admin_dashboard`.", ephemeral=True)
            return False
        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not (perms and perms.manage_guild):
            await interaction.response.send_message(embed=warning_embed("You need Manage Server permission."), ephemeral=True)
            return False
        return True

    def home_view(self) -> "AdminMenuView":
        return AdminMenuView(self.cog, self.guild_id, self.guild_name, self.owner_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
        log.exception("Dashboard button failed", exc_info=(type(error), error, error.__traceback__))
        await _send_dashboard_error(interaction, "That dashboard button failed. Open a fresh `/admin_dashboard` and check the bot console if it keeps happening.")


class AdminMenuView(AdminDashboardView):
    @discord.ui.button(label="Analysts", style=discord.ButtonStyle.primary, custom_id="signalflow:admin_analysts", row=0)
    async def analysts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=self.cog.admin_analysts_embed(self.guild_id),
            view=AnalystsAdminView(self.cog, self.guild_id, self.guild_name, self.owner_id),
        )

    @discord.ui.button(label="Examples", style=discord.ButtonStyle.primary, custom_id="signalflow:admin_examples", row=0)
    async def examples(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=self.cog.admin_examples_embed(self.guild_id),
            view=ExamplesAdminView(self.cog, self.guild_id, self.guild_name, self.owner_id),
        )

    @discord.ui.button(label="Positions", style=discord.ButtonStyle.primary, custom_id="signalflow:admin_positions", row=0)
    async def positions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=self.cog.admin_positions_embed(self.guild_id),
            view=PositionsAdminView(self.cog, self.guild_id, self.guild_name, self.owner_id),
        )

    @discord.ui.button(label="Testing", style=discord.ButtonStyle.secondary, custom_id="signalflow:admin_testing", row=0)
    async def testing(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=self.cog.admin_testing_embed(),
            view=TestingAdminView(self.cog, self.guild_id, self.guild_name, self.owner_id),
        )

    @discord.ui.button(label="Help", style=discord.ButtonStyle.secondary, custom_id="signalflow:admin_commands", row=1)
    async def commands(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_commands_embed(), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="signalflow:refresh_admin", row=1)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_overview_embed(self.guild_id, self.guild_name), view=self)


class AnalystsAdminView(AdminDashboardView):
    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="signalflow:analysts_back", row=0)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_overview_embed(self.guild_id, self.guild_name), view=self.home_view())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="signalflow:analysts_refresh", row=0)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_analysts_embed(self.guild_id), view=self)

    @discord.ui.button(label="Add Analyst", style=discord.ButtonStyle.success, custom_id="signalflow:add_analyst", row=1)
    async def add_analyst(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddAnalystModal(self.cog, self.guild_id, self.guild_name))

    @discord.ui.button(label="Remove Analyst", style=discord.ButtonStyle.danger, custom_id="signalflow:remove_analyst", row=1)
    async def remove_analyst(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(RemoveAnalystModal(self.cog))

    @discord.ui.button(label="Map Channel", style=discord.ButtonStyle.success, custom_id="signalflow:map_channel", row=1)
    async def map_channel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(SetChannelModal(self.cog))

    @discord.ui.button(label="Review Channel", style=discord.ButtonStyle.secondary, custom_id="signalflow:review_channel", row=1)
    async def review_channel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(ReviewChannelModal(self.cog))


class ExamplesAdminView(AdminDashboardView):
    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="signalflow:examples_back", row=0)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_overview_embed(self.guild_id, self.guild_name), view=self.home_view())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="signalflow:examples_refresh", row=0)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_examples_embed(self.guild_id), view=self)

    @discord.ui.button(label="Add Example", style=discord.ButtonStyle.success, custom_id="signalflow:add_example", row=1)
    async def add_example(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AddExampleModal(self.cog))

    @discord.ui.button(label="Remove Example", style=discord.ButtonStyle.danger, custom_id="signalflow:remove_example", row=1)
    async def remove_example(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(RemoveExampleModal(self.cog))

    @discord.ui.button(label="Import Help", style=discord.ButtonStyle.secondary, custom_id="signalflow:import_help", row=1)
    async def import_help(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(
            embed=warning_embed("Discord dashboards cannot open file upload pickers. Use `/admin_import_examples_csv` for CSV/TXT imports."),
            ephemeral=True,
        )


class PositionsAdminView(AdminDashboardView):
    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="signalflow:positions_back", row=0)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_overview_embed(self.guild_id, self.guild_name), view=self.home_view())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, custom_id="signalflow:positions_refresh", row=0)
    async def refresh(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_positions_embed(self.guild_id), view=self)

    @discord.ui.button(label="Clear Memory", style=discord.ButtonStyle.danger, custom_id="signalflow:clear_memory", row=1)
    async def clear_memory(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(ClearPositionsModal(self.cog))

    @discord.ui.button(label="Close Position", style=discord.ButtonStyle.danger, custom_id="signalflow:close_position", row=1)
    async def close_position(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(ClosePositionModal(self.cog))


class TestingAdminView(AdminDashboardView):
    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="signalflow:testing_back", row=0)
    async def back(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_overview_embed(self.guild_id, self.guild_name), view=self.home_view())

    @discord.ui.button(label="Test Alert", style=discord.ButtonStyle.primary, custom_id="signalflow:test_alert", row=1)
    async def test_alert(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(TestAlertModal(self.cog))

    @discord.ui.button(label="Daily Recap", style=discord.ButtonStyle.secondary, custom_id="signalflow:daily_recap", row=1)
    async def daily_recap(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.dashboard_daily_recap(interaction)


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database) -> None:
        self.bot = bot
        self.db = db

    def _channel_label(self, guild_id: int, channel_id: int | None) -> str:
        if not channel_id:
            return "Not set"
        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild else None
        if isinstance(channel, discord.TextChannel):
            return channel.mention
        return f"Missing channel ({channel_id})"

    def _finish_admin_embed(self, embed: discord.Embed) -> discord.Embed:
        embed.set_footer(text=FOOTER)
        return embed

    def admin_overview_embed(self, guild_id: int, guild_name: str) -> discord.Embed:
        analysts = self.db.list_analysts(guild_id)
        channel_map = self.db.get_channel_map(guild_id)
        review_channel_id = self.db.get_review_channel_id(guild_id)
        example_counts = self.db.count_classifier_examples_by_action(guild_id)
        open_alerts = self.db.count_open_entry_alerts(guild_id)
        open_user_positions = self.db.count_open_user_positions(guild_id)
        active = self.db.is_guild_active(guild_id)

        embed = discord.Embed(
            title="SignalFlow Admin",
            description=f"Server-local controls for **{guild_name}**.",
            color=BRAND_COLOR,
        )
        embed.add_field(
            name="Routing",
            value=(
                f"Status: **{'Active' if active else 'Disabled'}**\n"
                f"Analysts: **{len(analysts)}**\n"
                f"Mapped channels: **{len(channel_map)}**\n"
                f"Review channel: {self._channel_label(guild_id, review_channel_id)}"
            ),
            inline=False,
        )
        embed.add_field(name="Server Examples", value=_example_counts_text(example_counts), inline=False)
        embed.add_field(
            name="Position Memory",
            value=f"Analyst open trades `{open_alerts}`\nUser tracked positions `{open_user_positions}`",
            inline=False,
        )
        embed.add_field(
            name="Sections",
            value=(
                "`Analysts` - add analysts and map alert channels\n"
                "`Examples` - tune classifier examples\n"
                "`Positions` - inspect or clear bot memory\n"
                "`Testing` - send a fake routed alert"
            ),
            inline=False,
        )
        return self._finish_admin_embed(embed)

    def admin_analysts_embed(self, guild_id: int) -> discord.Embed:
        analysts = self.db.list_analysts(guild_id)
        channel_map: dict[int, list[int]] = {}
        for row in self.db.get_channel_map(guild_id):
            channel_map.setdefault(int(row["analyst_id"]), []).append(int(row["channel_id"]))
        lines = []
        for analyst in analysts:
            channel_ids = channel_map.get(analyst.id, [])
            label = analyst_label(analyst.discord_user_id, analyst.name)
            channels = ", ".join(self._channel_label(guild_id, channel_id) for channel_id in channel_ids)
            lines.append(f"- {label} - {channels}" if channels else f"- {label} - no channel")
        return list_embed("Analysts", lines, "No analysts configured yet.")

    def admin_examples_embed(self, guild_id: int) -> discord.Embed:
        counts = self.db.count_classifier_examples_by_action(guild_id)
        imports = self.db.list_classifier_example_imports(guild_id, limit=10)
        embed = discord.Embed(
            title="Server Examples",
            description=_example_counts_text(counts),
            color=BRAND_COLOR,
        )
        embed.add_field(
            name="Active Files",
            value=_active_imports_text(imports, counts),
            inline=False,
        )
        embed.add_field(
            name="Import",
            value=(
                "`/admin_import_examples_csv action:Entry file:entries.txt`\n"
                "`/admin_import_examples_csv action:Trim file:trims.txt`\n"
                "`/admin_import_examples_csv action:Close file:closes.txt`\n"
                "`/admin_import_examples_csv action:Ignore file:ignores.txt`"
            ),
            inline=False,
        )
        return self._finish_admin_embed(embed)

    def admin_positions_embed(self, guild_id: int) -> discord.Embed:
        analysts = self.db.list_analysts(guild_id)
        open_alert_counts = self.db.count_open_entry_alerts_by_analyst(guild_id)
        open_position_counts = self.db.count_open_user_positions_by_analyst(guild_id)
        lines = []
        for analyst in analysts:
            open_alerts = open_alert_counts.get(analyst.id, 0)
            open_user_positions = open_position_counts.get(analyst.id, 0)
            if open_alerts or open_user_positions:
                lines.append(f"- **{analyst.name}** - analyst `{open_alerts}` / users `{open_user_positions}`")

        embed = discord.Embed(
            title="Position Memory",
            description="\n".join(lines) if lines else "No active analyst positions or user-tracked positions.",
            color=BRAND_COLOR,
        )
        embed.add_field(
            name="Tools",
            value="Use `/current_positions` to inspect an analyst. Use the dashboard buttons to clear memory or close one position.",
            inline=False,
        )
        return self._finish_admin_embed(embed)

    def admin_commands_embed(self) -> discord.Embed:
        lines = [
            "`Analysts` - add/remove analysts, map channels, set review channel",
            "`Examples` - add/remove examples and see import help",
            "`Positions` - clear analyst memory or close one position",
            "`Testing` - send a fake alert through routing",
            "`Refresh` - reload the current dashboard page",
        ]
        return list_embed("Dashboard Actions", lines, "No actions found.")

    def admin_testing_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Testing",
            description="Send a fake alert through SignalFlow routing or preview today's recap card.",
            color=BRAND_COLOR,
        )
        embed.add_field(
            name="Format",
            value="`entry SPY 530C 5/24 1.20`\nActions: `entry`, `add`, `average_down`, `average_up`, `trim`, `close`, `roll_option`",
            inline=False,
        )
        return self._finish_admin_embed(embed)

    async def dashboard_daily_recap(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            settings = self.db.get_guild_settings(interaction.guild_id)
            brand = settings["recap_brand_name"] or settings["dashboard_display_name"] or interaction.guild.name
            footer = settings["recap_footer"] or f"{brand} | Premium Recap"
            output = Path("logs") / f"recap_{interaction.guild_id}_{date.today().isoformat()}.png"
            card = build_recap_card_from_database(
                self.db.path,
                guild_id=interaction.guild_id,
                recap_date=date.today(),
                brand=brand,
                footer=footer,
            )
            render_recap_card(card, output)
        except Exception:
            log.exception("Failed to render daily recap")
            await interaction.followup.send(embed=warning_embed("I could not generate the recap from the database."), ephemeral=True)
            return

        await interaction.followup.send(
            content="Today's database-backed recap preview:",
            file=discord.File(output),
            ephemeral=True,
        )

    async def dashboard_add_analyst(self, interaction: discord.Interaction, analyst_text: str, display_name: str) -> None:
        if not interaction.guild or not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        user = await _resolve_user(self.bot, interaction.guild, analyst_text)
        if user:
            name = display_name.strip() or analyst_display_name(user)
            self.db.add_analyst_user(interaction.guild_id, user.id, name)
            await interaction.response.send_message(embed=success_embed(f"Added analyst {user.mention} as **{name}**."), ephemeral=True)
            return

        name = display_name.strip() or _clean_name(analyst_text)
        if not name:
            await interaction.response.send_message(embed=warning_embed("Enter an analyst @, ID, or name."), ephemeral=True)
            return
        self.db.add_analyst(interaction.guild_id, name)
        await interaction.response.send_message(embed=success_embed(f"Added analyst **{name}**."), ephemeral=True)

    async def dashboard_remove_analyst(self, interaction: discord.Interaction, analyst_text: str) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        user_id = _snowflake(analyst_text)
        removed = self.db.remove_analyst_user(interaction.guild_id, user_id) if user_id else False
        analyst = _resolve_analyst_text(self.db, interaction.guild_id, analyst_text)
        if not removed and analyst:
            removed = self.db.remove_analyst(interaction.guild_id, analyst.name)
        await interaction.response.send_message(
            embed=success_embed("Analyst removed.") if removed else warning_embed("I could not find that analyst."),
            ephemeral=True,
        )

    async def dashboard_set_channel(self, interaction: discord.Interaction, analyst_text: str, channel_text: str) -> None:
        if not interaction.guild or not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        analyst = _resolve_analyst_text(self.db, interaction.guild_id, analyst_text)
        channel = _resolve_channel(interaction.guild, channel_text)
        if not analyst:
            await interaction.response.send_message(embed=warning_embed("I could not find that analyst. Add them first."), ephemeral=True)
            return
        if not channel:
            await interaction.response.send_message(embed=warning_embed("I could not find that text channel."), ephemeral=True)
            return

        previous = self.db.set_analyst_channel(interaction.guild_id, analyst.id, channel.id, channel.name)
        message = f"Added {channel.mention} to **{analyst.name}** routing."
        if previous:
            message = f"Reassigned {channel.mention} from **{previous.name}** to **{analyst.name}**."
        await interaction.response.send_message(embed=success_embed(message), ephemeral=True)

    async def dashboard_set_review_channel(self, interaction: discord.Interaction, channel_text: str) -> None:
        if not interaction.guild or not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        channel = _resolve_channel(interaction.guild, channel_text)
        if not channel:
            await interaction.response.send_message(embed=warning_embed("I could not find that text channel."), ephemeral=True)
            return
        self.db.set_review_channel(interaction.guild_id, channel.id, channel.name)
        await interaction.response.send_message(embed=success_embed(f"Review channel set to {channel.mention}."), ephemeral=True)

    async def dashboard_add_example(self, interaction: discord.Interaction, action_text: str, text: str) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        action = _normalize_action(action_text)
        clean_text = text.strip()
        if not action:
            await interaction.response.send_message(embed=warning_embed("Action must be entry, trim, close, or ignore."), ephemeral=True)
            return
        if not clean_text:
            await interaction.response.send_message(embed=warning_embed("Example text cannot be blank."), ephemeral=True)
            return

        example_id = self.db.add_classifier_example(interaction.guild_id, action, clean_text)
        await interaction.response.send_message(embed=success_embed(f"Saved example #{example_id} as `{action}`."), ephemeral=True)

    async def dashboard_remove_example(self, interaction: discord.Interaction, example_id_text: str) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return
        if not example_id_text.strip().isdigit():
            await interaction.response.send_message(embed=warning_embed("Example ID must be a number."), ephemeral=True)
            return

        example_id = int(example_id_text.strip())
        removed = self.db.delete_classifier_example(interaction.guild_id, example_id)
        await interaction.response.send_message(
            embed=success_embed(f"Removed example #{example_id}.") if removed else warning_embed("I could not find that example."),
            ephemeral=True,
        )

    async def dashboard_clear_positions(self, interaction: discord.Interaction, analyst_text: str) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        analyst = _resolve_analyst_text(self.db, interaction.guild_id, analyst_text)
        if not analyst:
            await interaction.response.send_message(embed=warning_embed("I could not find that analyst."), ephemeral=True)
            return

        alert_count = self.db.close_all_entry_alerts(interaction.guild_id, analyst.id)
        user_position_count = self.db.close_all_user_positions(interaction.guild_id, analyst.id)
        await interaction.response.send_message(
            embed=success_embed(
                f"Cleared **{analyst.name}** memory.\n"
                f"Analyst positions closed: `{alert_count}`\n"
                f"User tracked positions closed: `{user_position_count}`"
            ),
            ephemeral=True,
        )

    async def dashboard_close_position(self, interaction: discord.Interaction, analyst_text: str, ticker: str, contract: str) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        analyst = _resolve_analyst_text(self.db, interaction.guild_id, analyst_text)
        if not analyst:
            await interaction.response.send_message(embed=warning_embed("I could not find that analyst."), ephemeral=True)
            return

        clean_ticker = ticker.strip().upper()
        clean_contract = contract.upper().replace(" ", "") if contract.strip() else None
        if not clean_ticker:
            await interaction.response.send_message(embed=warning_embed("Ticker is required."), ephemeral=True)
            return

        count = self.db.close_matching_entry_alerts(interaction.guild_id, analyst.id, clean_ticker, clean_contract)
        await interaction.response.send_message(embed=success_embed(f"Closed {count} tracked position(s) for **{analyst.name}**."), ephemeral=True)

    async def dashboard_test_alert(self, interaction: discord.Interaction, analyst_text: str, details: str) -> None:
        if not interaction.guild or not interaction.guild_id:
            await interaction.response.send_message("Use this inside your server.", ephemeral=True)
            return

        analyst = _resolve_analyst_text(self.db, interaction.guild_id, analyst_text)
        if not analyst:
            await interaction.response.send_message(embed=warning_embed("I could not find that analyst."), ephemeral=True)
            return

        parts = details.split()
        if len(parts) < 5:
            await interaction.response.send_message(embed=warning_embed("Use: `entry SPY 530C 5/24 1.20`."), ephemeral=True)
            return
        action = _normalize_action(parts[0])
        if action == "ignore" or action not in {"entry", "add", "average_down", "average_up", "trim", "close", "roll_option"}:
            await interaction.response.send_message(embed=warning_embed("Action must be entry, add, average_down, average_up, trim, close, or roll_option."), ephemeral=True)
            return
        try:
            price = float(parts[4].replace("$", ""))
        except ValueError:
            await interaction.response.send_message(embed=warning_embed("Price must be a number."), ephemeral=True)
            return

        parsed = ParsedAlert(
            action=action,
            confidence="normal",
            ticker=parts[1].upper(),
            contract=parts[2].upper(),
            expiration=parts[3],
            price=price,
            raw_text=f"TEST {details}",
            trade_note="Day Trade",
        )
        await interaction.response.defer(ephemeral=True)
        routed = await self.bot.route_alert(interaction.guild, analyst, parsed, None, None)
        await interaction.followup.send(embed=success_embed(f"Test alert routed to {routed} user DM(s)."), ephemeral=True)

    @app_commands.command(name="admin_dashboard", description="Open the SignalFlow admin dashboard for this server.")
    @admin_only()
    async def admin_dashboard(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        self.db.update_guild_metadata(
            interaction.guild_id,
            interaction.guild.name,
            interaction.guild.icon.url if interaction.guild.icon else None,
        )
        view = AdminMenuView(self, interaction.guild_id, interaction.guild.name, interaction.user.id)
        await interaction.response.send_message(
            embed=self.admin_overview_embed(interaction.guild_id, interaction.guild.name),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(name="admin_web_link", description="Get this server's private SignalFlow web dashboard link.")
    @admin_only()
    async def admin_web_link(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        token = self.db.get_or_create_dashboard_token(interaction.guild_id)
        default_url = f"http://127.0.0.1:{os.getenv('DASHBOARD_PORT', '8080')}"
        base_url = os.getenv("PUBLIC_DASHBOARD_URL", default_url).strip().rstrip("/")
        url = f"{base_url}/?guild_id={interaction.guild_id}&token={token}"
        await interaction.response.send_message(
            embed=list_embed(
                "Private Web Dashboard",
                [
                    "Give this link only to this server's owner/admins.",
                    url,
                    "Rotate the token from the website settings if it leaks.",
                ],
                "No link available.",
            ),
            ephemeral=True,
        )

    @app_commands.command(name="admin_import_examples_csv", description="Import classifier examples from a labeled CSV or TXT file.")
    @app_commands.describe(
        action="Classification to apply to every row in this CSV.",
        file="CSV with a Content/text column, or TXT with one example per line.",
        max_per_action="Maximum examples to save from this file.",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Entry", value="entry"),
            app_commands.Choice(name="Trim", value="trim"),
            app_commands.Choice(name="Close", value="close"),
            app_commands.Choice(name="Ignore", value="ignore"),
        ]
    )
    @admin_only()
    async def admin_import_examples_csv(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        file: discord.Attachment,
        max_per_action: int = 100,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return
        filename = file.filename.lower()
        if not (filename.endswith(".csv") or filename.endswith(".txt")):
            await interaction.response.send_message(embed=warning_embed("Upload a `.csv` or `.txt` file."), ephemeral=True)
            return
        if file.size and file.size > 8_000_000:
            await interaction.response.send_message(embed=warning_embed("CSV is too large. Keep uploads under 8 MB."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            data = await file.read()
            if filename.endswith(".txt"):
                examples, stats = examples_from_txt_bytes(data, per_action_limit=max_per_action, fixed_action=action.value)
            else:
                examples, stats = examples_from_csv_bytes(data, per_action_limit=max_per_action, fixed_action=action.value)
        except ValueError as exc:
            await interaction.followup.send(embed=warning_embed(str(exc)), ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(embed=warning_embed("I could not read that CSV export."), ephemeral=True)
            return

        saved = self.db.add_classifier_examples(interaction.guild_id, examples)
        self.db.record_classifier_example_import(
            interaction.guild_id,
            action.value,
            file.filename,
            "txt" if filename.endswith(".txt") else "csv",
            saved,
            int(stats.get("rows", 0)),
        )
        lines = [
            f"Saved `{saved}` `{action.value}` examples from `{file.filename}`.",
            f"Scanned `{stats.get('rows', 0)}` rows.",
        ]
        await interaction.followup.send(embed=success_embed("\n".join(lines)), ephemeral=True)

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(embed=warning_embed("You need Manage Server permission for admin commands."), ephemeral=True)
            return
        raise error


async def setup(bot: commands.Bot, db: Database) -> None:
    await bot.add_cog(AdminCommands(bot, db))
