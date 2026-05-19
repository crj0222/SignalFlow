import discord
from discord import app_commands
from discord.ext import commands

from database import Database
from embeds import list_embed, success_embed, warning_embed
from example_importer import examples_from_csv_bytes
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

    @app_commands.command(name="admin_import_examples_csv", description="Import classifier examples from a Discord CSV export.")
    @app_commands.describe(
        file="Discord export CSV with a Content column.",
        max_per_action="Maximum examples to save per action from this file.",
    )
    @admin_only()
    async def admin_import_examples_csv(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        max_per_action: int = 30,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("Use this command inside your server.", ephemeral=True)
            return
        if not file.filename.lower().endswith(".csv"):
            await interaction.response.send_message(embed=warning_embed("Upload a `.csv` file."), ephemeral=True)
            return
        if file.size and file.size > 8_000_000:
            await interaction.response.send_message(embed=warning_embed("CSV is too large. Keep uploads under 8 MB."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            data = await file.read()
            examples, stats = examples_from_csv_bytes(data, per_action_limit=max_per_action)
        except ValueError as exc:
            await interaction.followup.send(embed=warning_embed(str(exc)), ephemeral=True)
            return
        except Exception:
            await interaction.followup.send(embed=warning_embed("I could not read that CSV export."), ephemeral=True)
            return

        saved = self.db.add_classifier_examples(interaction.guild_id, examples)
        lines = [
            f"Saved `{saved}` examples from `{file.filename}`.",
            f"Entry `{stats.get('entry', 0)}`  Trim `{stats.get('trim', 0)}`  Close `{stats.get('close', 0)}`  Ignore `{stats.get('ignore', 0)}`",
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
