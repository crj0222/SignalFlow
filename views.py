import re

import discord

from database import Database
from embeds import success_embed, warning_embed
from models import Analyst


ANALYST_EMOJIS = [
    "🔵",
    "🟢",
    "🟣",
    "🟡",
    "🔴",
    "⚪",
    "⚫",
    "⭐",
    "🔥",
    "💎",
    "📈",
    "🎯",
    "🚀",
    "⚡",
    "🏆",
    "🔔",
    "✅",
    "🧭",
    "💬",
    "📌",
]


MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def clean_analyst_name(name: str) -> str:
    value = name.strip()
    mention = MENTION_RE.match(value)
    if mention:
        return mention.group(1)
    return value[1:] if value.startswith("@") else value


def analyst_key_lines(analysts: list[Analyst], selected_ids: set[int]) -> list[str]:
    lines = []
    for index, analyst in enumerate(analysts[: len(ANALYST_EMOJIS)]):
        state = "On" if analyst.id in selected_ids else "Off"
        lines.append(f"{ANALYST_EMOJIS[index]} **{clean_analyst_name(analyst.name)}** - {state}")
    return lines


class AnalystToggleButton(discord.ui.Button):
    def __init__(self, db: Database, guild_id: int, analyst: Analyst, emoji: str, selected_ids: set[int]) -> None:
        self.db = db
        self.guild_id = guild_id
        self.analyst = analyst
        super().__init__(
            label=clean_analyst_name(analyst.name)[:80],
            emoji=emoji,
            style=discord.ButtonStyle.success if analyst.id in selected_ids else discord.ButtonStyle.secondary,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.db.list_user_subscriptions(self.guild_id, interaction.user.id)
        selected_ids = {analyst.id for analyst in selected}
        if self.analyst.id in selected_ids:
            selected_ids.remove(self.analyst.id)
        else:
            selected_ids.add(self.analyst.id)

        self.db.replace_subscriptions(self.guild_id, interaction.user.id, selected_ids)
        analysts = getattr(self.view, "analysts", self.db.list_analysts(self.guild_id))
        embed = build_analyst_picker_embed(analysts, selected_ids)
        await interaction.response.edit_message(
            embed=embed,
            view=AnalystSelectView(self.db, self.guild_id, analysts, selected_ids),
        )


def build_analyst_picker_embed(analysts: list[Analyst], selected_ids: set[int]) -> discord.Embed:
    shown = analysts[: len(ANALYST_EMOJIS)]
    lines = analyst_key_lines(shown, selected_ids)
    description = "\n".join(lines) if lines else "No analysts are configured yet."
    if len(analysts) > len(ANALYST_EMOJIS):
        description += f"\n\nShowing first {len(ANALYST_EMOJIS)} analysts. Ask an admin to remove inactive analysts if needed."

    embed = discord.Embed(
        title="Select Analysts",
        description=description,
        color=0x2F80ED,
    )
    embed.set_footer(text="Tap a button to turn that analyst on or off.")
    return embed


class AnalystSelectView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, analysts: list[Analyst], selected_ids: set[int]) -> None:
        super().__init__(timeout=180)
        self.db = db
        self.guild_id = guild_id
        self.analysts = analysts
        for index, analyst in enumerate(analysts[: len(ANALYST_EMOJIS)]):
            self.add_item(
                AnalystToggleButton(
                    db=db,
                    guild_id=guild_id,
                    analyst=analyst,
                    emoji=ANALYST_EMOJIS[index],
                    selected_ids=selected_ids,
                )
            )


class EntryAlertView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, analyst_id: int, alert_id: int) -> None:
        super().__init__(timeout=None)
        self.db = db
        self.guild_id = guild_id
        self.analyst_id = analyst_id
        self.alert_id = alert_id

    @discord.ui.button(label="Took Trade", style=discord.ButtonStyle.success, custom_id="signalflow:took_trade")
    async def took_trade(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.db.is_alert_open(self.alert_id):
            await interaction.response.send_message(
                embed=warning_embed("This trade is no longer open, so it cannot be marked as taken."),
                ephemeral=True,
            )
            if interaction.message:
                try:
                    await interaction.message.edit(view=None)
                except discord.HTTPException:
                    pass
            return

        self.db.mark_alert_action(self.alert_id, self.guild_id, interaction.user.id, "took")
        self.db.open_position(self.guild_id, interaction.user.id, self.analyst_id, self.alert_id)
        await interaction.response.send_message(embed=success_embed("Marked as taken and added to your open positions."), ephemeral=True)

    @discord.ui.button(label="Manage Alerts", style=discord.ButtonStyle.primary, custom_id="signalflow:manage_alerts")
    async def manage_alerts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message("Open your server and use `/select_analysts` to update your alerts.", ephemeral=True)


class AutoTakenEntryAlertView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Auto Taken", style=discord.ButtonStyle.success, disabled=True, custom_id="signalflow:auto_taken")
    async def auto_taken(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.defer()

    @discord.ui.button(label="Manage Alerts", style=discord.ButtonStyle.primary, custom_id="signalflow:auto_manage_alerts")
    async def manage_alerts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message("Open your server and use `/select_analysts` to update your alerts.", ephemeral=True)


class ExitAlertView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, position_id: int, alert_id: int) -> None:
        super().__init__(timeout=None)
        self.db = db
        self.guild_id = guild_id
        self.position_id = position_id
        self.alert_id = alert_id

    @discord.ui.button(label="Close Position", style=discord.ButtonStyle.danger, custom_id="signalflow:close_position")
    async def close_position(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        closed = self.db.close_position(self.position_id, interaction.user.id)
        if closed:
            await interaction.response.send_message(
                embed=success_embed("Position closed. SignalFlow will stop routing alerts for this tracked trade."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(embed=warning_embed("I could not find an open position to close."), ephemeral=True)


class StopAlertView(discord.ui.View):
    def __init__(self, db: Database, guild_id: int, position_id: int, alert_id: int) -> None:
        super().__init__(timeout=None)
        self.db = db
        self.guild_id = guild_id
        self.position_id = position_id
        self.alert_id = alert_id

    @discord.ui.button(label="Stopped Out", style=discord.ButtonStyle.danger, custom_id="signalflow:stopped_out")
    async def stopped_out(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.db.mark_alert_action(self.alert_id, self.guild_id, interaction.user.id, "stopped_out")
        closed = self.db.close_position(self.position_id, interaction.user.id)
        if closed:
            await interaction.response.send_message(embed=success_embed("Marked as stopped out and closed."), ephemeral=True)
        else:
            await interaction.response.send_message(embed=warning_embed("I could not find an open position to close."), ephemeral=True)

    @discord.ui.button(label="Close Position", style=discord.ButtonStyle.danger, custom_id="signalflow:stop_close_position")
    async def close_position(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        closed = self.db.close_position(self.position_id, interaction.user.id)
        if closed:
            await interaction.response.send_message(
                embed=success_embed("Position closed. SignalFlow will stop routing alerts for this tracked trade."),
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(embed=warning_embed("I could not find an open position to close."), ephemeral=True)
