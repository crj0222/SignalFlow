import discord
from discord import app_commands
from discord.ext import commands

from database import Database
from embeds import BRAND_COLOR, list_embed, success_embed, warning_embed
from example_importer import examples_from_csv_bytes, examples_from_txt_bytes
from models import ParsedAlert


EXAMPLE_ACTION_LABELS = {
    "entry": "Entry",
    "add": "Add",
    "average_down": "Avg Down",
    "average_up": "Avg Up",
    "trim": "Trim",
    "close": "Close",
    "roll_option": "Roll",
    "ignore": "Ignore",
}


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


def _example_counts_text(counts: dict[str, int]) -> str:
    total = sum(counts.values())
    if total == 0:
        return "No server examples imported yet."
    parts = [f"{label} `{counts.get(action, 0)}`" for action, label in EXAMPLE_ACTION_LABELS.items()]
    return "  ".join(parts) + f"\nTotal `{total}`"


def _short_example(text: str, limit: int = 92) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else f"{clean[: limit - 3]}..."


class AdminMenuView(discord.ui.View):
    def __init__(self, cog: "AdminCommands", guild_id: int, guild_name: str, owner_id: int) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.guild_id = guild_id
        self.guild_name = guild_name
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Open your own admin menu with `/admin_menu`.", ephemeral=True)
            return False
        perms = interaction.user.guild_permissions if isinstance(interaction.user, discord.Member) else None
        if not (perms and perms.manage_guild):
            await interaction.response.send_message(embed=warning_embed("You need Manage Server permission."), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, custom_id="signalflow:admin_overview")
    async def overview(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_overview_embed(self.guild_id, self.guild_name), view=self)

    @discord.ui.button(label="Analysts", style=discord.ButtonStyle.secondary, custom_id="signalflow:admin_analysts")
    async def analysts(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_analysts_embed(self.guild_id), view=self)

    @discord.ui.button(label="Examples", style=discord.ButtonStyle.secondary, custom_id="signalflow:admin_examples")
    async def examples(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_examples_embed(self.guild_id), view=self)

    @discord.ui.button(label="Positions", style=discord.ButtonStyle.secondary, custom_id="signalflow:admin_positions")
    async def positions(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_positions_embed(self.guild_id), view=self)

    @discord.ui.button(label="Commands", style=discord.ButtonStyle.secondary, custom_id="signalflow:admin_commands")
    async def commands(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=self.cog.admin_commands_embed(), view=self)


class AdminCommands(commands.Cog):
    def __init__(self, bot: commands.Bot, db: Database) -> None:
        self.bot = bot
        self.db = db

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
                f"Status `{'Active' if active else 'Disabled'}`\n"
                f"Analysts `{len(analysts)}`\n"
                f"Mapped channels `{len(channel_map)}`\n"
                f"Review channel `{f'<#{review_channel_id}>' if review_channel_id else 'Not set'}`"
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
            name="Next Actions",
            value=(
                "`/admin_add_analyst`\n"
                "`/admin_set_channel`\n"
                "`/admin_import_examples_csv`\n"
                "`/admin_clear_positions`"
            ),
            inline=False,
        )
        return embed

    def admin_analysts_embed(self, guild_id: int) -> discord.Embed:
        analysts = self.db.list_analysts(guild_id)
        channel_map = {row["name"]: row["channel_id"] for row in self.db.get_channel_map(guild_id)}
        lines = []
        for analyst in analysts:
            channel_id = channel_map.get(analyst.name)
            label = analyst_label(analyst.discord_user_id, analyst.name)
            lines.append(f"- {label} - <#{channel_id}>" if channel_id else f"- {label} - no channel")
        return list_embed("Analysts", lines, "No analysts configured yet.")

    def admin_examples_embed(self, guild_id: int) -> discord.Embed:
        counts = self.db.count_classifier_examples_by_action(guild_id)
        rows = self.db.list_classifier_examples(guild_id, limit=10)
        embed = discord.Embed(
            title="Server Examples",
            description=_example_counts_text(counts),
            color=BRAND_COLOR,
        )
        recent = [f"#{row['id']} `{row['action']}` - {_short_example(row['example_text'])}" for row in rows]
        embed.add_field(
            name="Recent",
            value="\n".join(recent) if recent else "No examples imported yet.",
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
        return embed

    def admin_positions_embed(self, guild_id: int) -> discord.Embed:
        analysts = self.db.list_analysts(guild_id)
        lines = []
        for analyst in analysts:
            open_alerts = self.db.count_open_entry_alerts(guild_id, analyst.id)
            open_user_positions = self.db.count_open_user_positions(guild_id, analyst.id)
            if open_alerts or open_user_positions:
                lines.append(f"- **{analyst.name}** - analyst `{open_alerts}` / users `{open_user_positions}`")

        embed = discord.Embed(
            title="Position Memory",
            description="\n".join(lines) if lines else "No active analyst positions or user-tracked positions.",
            color=BRAND_COLOR,
        )
        embed.add_field(
            name="Tools",
            value="Use `/current_positions` to inspect an analyst or `/admin_clear_positions` to wipe an analyst's active memory.",
            inline=False,
        )
        return embed

    def admin_commands_embed(self) -> discord.Embed:
        lines = [
            "`/admin_add_analyst` - add an analyst bot/user",
            "`/admin_remove_analyst` - disable an analyst",
            "`/admin_set_channel` - map analyst to channel",
            "`/admin_set_review_channel` - set medium-confidence review channel",
            "`/admin_import_examples_csv` - import labeled examples",
            "`/admin_add_example` - add one example",
            "`/admin_list_examples` - list saved examples",
            "`/admin_clear_positions` - wipe analyst active memory",
            "`/admin_test_alert` - route a fake alert",
        ]
        return list_embed("Admin Commands", lines, "No commands found.")

    @app_commands.command(name="admin_menu", description="Open the SignalFlow admin menu for this server.")
    @admin_only()
    async def admin_menu(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id or not interaction.guild:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        view = AdminMenuView(self, interaction.guild_id, interaction.guild.name, interaction.user.id)
        await interaction.response.send_message(
            embed=self.admin_overview_embed(interaction.guild_id, interaction.guild.name),
            view=view,
            ephemeral=True,
        )

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

    @app_commands.command(name="admin_add_example", description="Teach SignalFlow one server-specific classifier example.")
    @app_commands.describe(
        action="Correct classification for this example.",
        text="Exact alert/message wording to use as an example.",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Entry", value="entry"),
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Average Down", value="average_down"),
            app_commands.Choice(name="Average Up", value="average_up"),
            app_commands.Choice(name="Trim", value="trim"),
            app_commands.Choice(name="Close", value="close"),
            app_commands.Choice(name="Roll Option", value="roll_option"),
            app_commands.Choice(name="Ignore", value="ignore"),
        ]
    )
    @admin_only()
    async def admin_add_example(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        text: str,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        clean_text = text.strip()
        if not clean_text:
            await interaction.response.send_message(embed=warning_embed("Example text cannot be blank."), ephemeral=True)
            return
        if len(clean_text) > 900:
            await interaction.response.send_message(embed=warning_embed("Keep examples under 900 characters."), ephemeral=True)
            return

        example_id = self.db.add_classifier_example(interaction.guild_id, action.value, clean_text)
        await interaction.response.send_message(
            embed=success_embed(f"Saved example #{example_id} as `{action.value}`."),
            ephemeral=True,
        )

    @app_commands.command(name="admin_list_examples", description="List this server's classifier examples.")
    @admin_only()
    async def admin_list_examples(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        rows = self.db.list_classifier_examples(interaction.guild_id, limit=25)
        lines = []
        for row in rows:
            text = row["example_text"].replace("\n", " ")
            if len(text) > 90:
                text = f"{text[:87]}..."
            lines.append(f"#{row['id']} `{row['action']}` - {text}")

        await interaction.response.send_message(
            embed=list_embed("Classifier Examples", lines, "No classifier examples saved yet."),
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
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Average Down", value="average_down"),
            app_commands.Choice(name="Average Up", value="average_up"),
            app_commands.Choice(name="Trim", value="trim"),
            app_commands.Choice(name="Close", value="close"),
            app_commands.Choice(name="Roll Option", value="roll_option"),
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
        lines = [
            f"Saved `{saved}` `{action.value}` examples from `{file.filename}`.",
            f"Scanned `{stats.get('rows', 0)}` rows.",
        ]
        await interaction.followup.send(embed=success_embed("\n".join(lines)), ephemeral=True)

    @app_commands.command(name="admin_remove_example", description="Remove one classifier example by ID.")
    @admin_only()
    async def admin_remove_example(self, interaction: discord.Interaction, example_id: int) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return

        removed = self.db.delete_classifier_example(interaction.guild_id, example_id)
        embed = success_embed(f"Removed example #{example_id}.") if removed else warning_embed("I could not find that example.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

        alert_count = self.db.close_all_entry_alerts(interaction.guild_id, analyst_row.id)
        user_position_count = self.db.close_all_user_positions(interaction.guild_id, analyst_row.id)
        await interaction.response.send_message(
            embed=success_embed(
                f"Cleared **{analyst_row.name}** memory.\n"
                f"Analyst positions closed: `{alert_count}`\n"
                f"User tracked positions closed: `{user_position_count}`"
            ),
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
        if normalized_action in {"exit", "stop"}:
            normalized_action = "close"
        valid_actions = {"entry", "add", "average_down", "average_up", "trim", "close", "roll_option"}
        if normalized_action not in valid_actions:
            await interaction.response.send_message(
                embed=warning_embed("Action must be `entry`, `add`, `average_down`, `average_up`, `trim`, `close`, or `roll_option`."),
                ephemeral=True,
            )
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
