import re
from dataclasses import replace

import discord

from database import Database
from embeds import BRAND_COLOR, FOOTER, success_embed, warning_embed
from models import Analyst, ParsedAlert


ANALYST_EMOJIS = [
    "\U0001f535",
    "\U0001f7e2",
    "\U0001f7e3",
    "\U0001f7e1",
    "\U0001f534",
    "\u26aa",
    "\u26ab",
    "\u2b50",
    "\U0001f525",
    "\U0001f48e",
    "\U0001f4c8",
    "\U0001f3af",
    "\U0001f680",
    "\u26a1",
    "\U0001f3c6",
    "\U0001f514",
    "\u2705",
    "\U0001f9ed",
    "\U0001f4ac",
    "\U0001f4cc",
]


MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def clean_analyst_name(name: str) -> str:
    value = " ".join((name or "").split()).strip()
    mention = MENTION_RE.match(value)
    if mention:
        return f"Analyst {mention.group(1)[-4:]}"
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
        color=BRAND_COLOR,
    )
    embed.set_footer(text=f"{FOOTER} \u2022 Tap a button to toggle alerts")
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


class ReviewAlertView(discord.ui.View):
    def __init__(
        self,
        db: Database,
        guild_id: int,
        analyst: Analyst,
        parsed: ParsedAlert,
        source_channel_id: int | None = None,
        source_message_id: int | None = None,
    ) -> None:
        super().__init__(timeout=3600)
        self.db = db
        self.guild_id = guild_id
        self.analyst = analyst
        self.parsed = parsed
        self.source_channel_id = source_channel_id
        self.source_message_id = source_message_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not (perms and perms.manage_guild):
            await interaction.response.send_message(
                embed=warning_embed("You need Manage Server permission to review alerts."),
                ephemeral=True,
            )
            return False
        return True

    def _example_text(self) -> str:
        text = " ".join((self.parsed.raw_text or "").split()).strip()
        return text[:900]

    def _save_example(self, action: str) -> int | None:
        if action not in {"entry", "trim", "close", "ignore"}:
            return None
        text = self._example_text()
        if not text:
            return None
        return self.db.add_classifier_example(self.guild_id, action, text)

    async def _disable_review(self, interaction: discord.Interaction, status: str) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if interaction.message:
            try:
                embed = interaction.message.embeds[0] if interaction.message.embeds else None
                if embed:
                    embed.add_field(name="Review Result", value=status, inline=False)
                    await interaction.message.edit(embed=embed, view=self)
                else:
                    await interaction.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def _approve(self, interaction: discord.Interaction, action: str, save_example: bool = True) -> None:
        if not interaction.guild:
            await interaction.response.send_message(embed=warning_embed("Use this inside the server."), ephemeral=True)
            return

        reviewed = replace(self.parsed, action=action, confidence="high")
        example_id = self._save_example(action) if save_example else None
        await interaction.response.defer(ephemeral=True)
        routed = await interaction.client.route_alert(  # type: ignore[attr-defined]
            interaction.guild,
            self.analyst,
            reviewed,
            self.source_channel_id,
            self.source_message_id,
        )
        saved_text = f"\nSaved example `#{example_id}` as `{action}`." if example_id else ""
        await self._disable_review(interaction, f"Approved as `{action}` by {interaction.user.mention}. Routed to `{routed}` user(s).")
        await interaction.followup.send(
            embed=success_embed(f"Approved as `{action}` and routed to `{routed}` user DM(s).{saved_text}"),
            ephemeral=True,
        )

    @discord.ui.button(label="Send Detected", style=discord.ButtonStyle.primary, custom_id="signalflow:review_send_detected", row=0)
    async def send_detected(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._approve(interaction, self.parsed.action, save_example=self.parsed.action in {"entry", "trim", "close"})

    @discord.ui.button(label="Entry", style=discord.ButtonStyle.success, custom_id="signalflow:review_entry", row=1)
    async def entry(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._approve(interaction, "entry")

    @discord.ui.button(label="Trim", style=discord.ButtonStyle.success, custom_id="signalflow:review_trim", row=1)
    async def trim(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._approve(interaction, "trim")

    @discord.ui.button(label="Close", style=discord.ButtonStyle.success, custom_id="signalflow:review_close", row=1)
    async def close(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._approve(interaction, "close")

    @discord.ui.button(label="Ignore", style=discord.ButtonStyle.danger, custom_id="signalflow:review_ignore", row=1)
    async def ignore(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        example_id = self._save_example("ignore")
        saved_text = f" Saved example `#{example_id}` as `ignore`." if example_id else ""
        await interaction.response.defer(ephemeral=True)
        await self._disable_review(interaction, f"Ignored by {interaction.user.mention}.{saved_text}")
        await interaction.followup.send(
            embed=success_embed(f"Ignored.{saved_text}"),
            ephemeral=True,
        )


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
