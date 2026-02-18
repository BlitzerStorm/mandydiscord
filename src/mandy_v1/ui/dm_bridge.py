from __future__ import annotations

import discord


class DMBridgeUserSelect(discord.ui.Select):
    def __init__(self, bot: discord.Client, options: list[discord.SelectOption]):
        super().__init__(
            placeholder="Select a user to open DM bridge",
            min_values=1,
            max_values=1,
            options=options[:25],
            custom_id="mandy:dm_bridge:user_select",
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        handler = getattr(self.bot, "handle_dm_bridge_user_pick", None)
        if handler is None:
            await interaction.response.send_message("DM bridge handler unavailable.", ephemeral=True)
            return
        raw = str(self.values[0]).strip()
        await handler(interaction=interaction, raw_user_id=raw)


class DMBridgeUserModal(discord.ui.Modal):
    def __init__(self, bot: discord.Client):
        super().__init__(title="Open DM Bridge")
        self.bot = bot
        self.user_id = discord.ui.TextInput(
            label="User ID (UUID)",
            placeholder="Paste Discord user ID (numbers only).",
            min_length=5,
            max_length=30,
            required=True,
        )
        self.add_item(self.user_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        handler = getattr(self.bot, "handle_dm_bridge_user_pick", None)
        if handler is None:
            await interaction.response.send_message("DM bridge handler unavailable.", ephemeral=True)
            return
        raw = str(self.user_id.value or "").strip()
        await handler(interaction=interaction, raw_user_id=raw)


class DMBridgeUserView(discord.ui.View):
    def __init__(self, bot: discord.Client, options: list[discord.SelectOption]):
        super().__init__(timeout=180)
        self.bot = bot
        if options:
            self.add_item(DMBridgeUserSelect(bot, options))

    @discord.ui.button(label="Paste User ID", style=discord.ButtonStyle.primary)
    async def paste_user_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(DMBridgeUserModal(self.bot))


class DMBridgeControlView(discord.ui.View):
    def __init__(self, bot: discord.Client, user_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.user_id = int(user_id)

        is_active = True
        ai_enabled = True
        bridge = getattr(self.bot, "dm_bridges", None)
        if bridge is not None:
            is_active = bool(getattr(bridge, "is_active")(self.user_id))
            ai_enabled = bool(getattr(bridge, "is_ai_enabled")(self.user_id))

        ai_button = discord.ui.Button(
            label=f"AI Response: {'ON' if ai_enabled else 'OFF'}",
            style=discord.ButtonStyle.success if ai_enabled else discord.ButtonStyle.secondary,
            custom_id=f"mandy:dm_bridge:{self.user_id}:toggle_ai",
        )
        ai_button.callback = self._toggle_ai
        self.add_item(ai_button)

        refresh_button = discord.ui.Button(
            label="Refresh Full History",
            style=discord.ButtonStyle.primary,
            custom_id=f"mandy:dm_bridge:{self.user_id}:refresh",
        )
        refresh_button.callback = self._refresh_history
        self.add_item(refresh_button)

        bridge_button = discord.ui.Button(
            label="Close DM Bridge" if is_active else "Open DM Bridge",
            style=discord.ButtonStyle.danger if is_active else discord.ButtonStyle.success,
            custom_id=f"mandy:dm_bridge:{self.user_id}:toggle_open",
        )
        bridge_button.callback = self._toggle_open
        self.add_item(bridge_button)

    async def _toggle_ai(self, interaction: discord.Interaction) -> None:
        await self._dispatch(interaction, action="toggle_ai")

    async def _refresh_history(self, interaction: discord.Interaction) -> None:
        await self._dispatch(interaction, action="refresh")

    async def _toggle_open(self, interaction: discord.Interaction) -> None:
        await self._dispatch(interaction, action="toggle_open")

    async def _dispatch(self, interaction: discord.Interaction, *, action: str) -> None:
        handler = getattr(self.bot, "handle_dm_bridge_control_action", None)
        if handler is None:
            await interaction.response.send_message("DM bridge control handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction, user_id=self.user_id, action=action)
