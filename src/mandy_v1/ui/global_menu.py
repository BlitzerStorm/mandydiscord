from __future__ import annotations

import discord


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


class GlobalMenuView(discord.ui.View):
    def __init__(self, bot: discord.Client):
        super().__init__(timeout=None)
        self.bot = bot

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
