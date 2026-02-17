from __future__ import annotations

import discord


class GlobalSatelliteSelect(discord.ui.Select):
    def __init__(self, bot: discord.Client, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Pick a satellite",
            min_values=1,
            max_values=1,
            options=options[:25],
            custom_id="mandy:global_menu:satellite_select",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        raw = self.values[0].strip()
        if not raw.isdigit():
            await interaction.response.send_message("Invalid satellite selection.", ephemeral=True)
            return
        handler = getattr(self.bot, "open_global_satellite_menu", None)
        if handler is None:
            await interaction.response.send_message("Global menu handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction, satellite_guild_id=int(raw))


class GlobalSatellitePickerModal(discord.ui.Modal):
    def __init__(self, bot: discord.Client):
        super().__init__(title="Open Satellite Controls")
        self.bot = bot
        self.satellite_id = discord.ui.TextInput(
            label="Satellite Server ID",
            placeholder="Enter the server ID you want to control.",
            min_length=2,
            max_length=30,
            required=True,
        )
        self.add_item(self.satellite_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.satellite_id.value.strip()
        if not raw.isdigit():
            await interaction.response.send_message("Invalid server ID.", ephemeral=True)
            return
        handler = getattr(self.bot, "open_global_satellite_menu", None)
        if handler is None:
            await interaction.response.send_message("Global menu handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction, satellite_guild_id=int(raw))


class PromptInjectionModal(discord.ui.Modal):
    def __init__(self, bot: discord.Client):
        super().__init__(title="Inject Prompt")
        self.bot = bot
        self.scope = discord.ui.TextInput(
            label="Scope",
            placeholder="global or satellite guild id",
            min_length=1,
            max_length=30,
            required=True,
            default="global",
        )
        self.learning_mode = discord.ui.TextInput(
            label="Learning Mode",
            placeholder="off | light | full",
            min_length=2,
            max_length=10,
            required=True,
            default="full",
        )
        self.prompt_text = discord.ui.TextInput(
            label="Prompt (Hard Priority)",
            style=discord.TextStyle.paragraph,
            placeholder="Highest-priority behavior instructions for Mandy in this scope.",
            min_length=0,
            max_length=4000,
            required=False,
        )
        self.add_item(self.scope)
        self.add_item(self.learning_mode)
        self.add_item(self.prompt_text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        handler = getattr(self.bot, "global_menu_inject_prompt", None)
        if handler is None:
            await interaction.response.send_message("Global menu handler unavailable.", ephemeral=True)
            return
        await handler(
            interaction=interaction,
            scope=str(self.scope.value or "").strip(),
            learning_mode=str(self.learning_mode.value or "").strip(),
            prompt_text=str(self.prompt_text.value or ""),
        )


class GlobalMenuView(discord.ui.View):
    def __init__(self, bot: discord.Client):
        super().__init__(timeout=None)
        self.bot = bot
        self._maybe_add_satellite_select()

    def _can_run(self, interaction: discord.Interaction, min_tier: int) -> bool:
        soc = getattr(self.bot, "soc", None)
        if soc is None:
            return False
        return bool(soc.can_run(interaction.user, min_tier))

    def _maybe_add_satellite_select(self) -> None:
        store = getattr(self.bot, "store", None)
        servers = None
        if store is not None:
            servers = store.data.get("mirrors", {}).get("servers", {})
        if not isinstance(servers, dict) or not servers:
            return
        options: list[discord.SelectOption] = []
        valid_ids = [int(guild_id) for guild_id in servers.keys() if str(guild_id).isdigit()]
        for gid in sorted(valid_ids):
            guild = self.bot.get_guild(gid)
            label = guild.name[:95] if guild else f"Satellite {gid}"
            options.append(discord.SelectOption(label=label, value=str(gid), description=str(gid)))
        if options:
            self.add_item(GlobalSatelliteSelect(self.bot, options))

    @discord.ui.button(
        label="Open Satellite Controls",
        style=discord.ButtonStyle.primary,
        custom_id="mandy:global_menu:open_satellite",
    )
    async def open_satellite(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GlobalSatellitePickerModal(self.bot))

    @discord.ui.button(
        label="List Satellites",
        style=discord.ButtonStyle.secondary,
        custom_id="mandy:global_menu:list_satellites",
    )
    async def list_satellites(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._can_run(interaction, 50):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        handler = getattr(self.bot, "global_menu_list_satellites", None)
        if handler is None:
            await interaction.response.send_message("Global menu handler unavailable.", ephemeral=True)
            return
        text = await handler()
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(
        label="Health Snapshot",
        style=discord.ButtonStyle.success,
        custom_id="mandy:global_menu:health",
    )
    async def health_snapshot(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._can_run(interaction, 50):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        handler = getattr(self.bot, "global_menu_health_snapshot", None)
        if handler is None:
            await interaction.response.send_message("Global menu handler unavailable.", ephemeral=True)
            return
        text = await handler()
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    @discord.ui.button(
        label="Refresh Menu Panel",
        style=discord.ButtonStyle.secondary,
        custom_id="mandy:global_menu:refresh",
    )
    async def refresh_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        handler = getattr(self.bot, "refresh_global_menu_panel", None)
        if handler is None:
            await interaction.response.send_message("Global menu handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction)

    @discord.ui.button(
        label="Inject Prompt",
        style=discord.ButtonStyle.primary,
        custom_id="mandy:global_menu:inject_prompt",
    )
    async def inject_prompt(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._can_run(interaction, 90):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.send_modal(PromptInjectionModal(self.bot))

    @discord.ui.button(
        label="Self Check",
        style=discord.ButtonStyle.danger,
        custom_id="mandy:global_menu:selfcheck",
    )
    async def self_check(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        handler = getattr(self.bot, "global_menu_selfcheck", None)
        if handler is None:
            await interaction.response.send_message("Global menu handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction)
