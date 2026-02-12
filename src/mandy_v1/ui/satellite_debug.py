from __future__ import annotations

import discord


class PermissionRequestModal(discord.ui.Modal):
    def __init__(self, bot: discord.Client, satellite_guild_id: int, action: str):
        super().__init__(title="Request Permission")
        self.bot = bot
        self.satellite_guild_id = satellite_guild_id
        self.action = action
        self.reason = discord.ui.TextInput(
            label="What do you need?",
            style=discord.TextStyle.paragraph,
            placeholder="Describe what you want Mandy to do and why.",
            max_length=1000,
            required=True,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        handler = getattr(self.bot, "submit_permission_request", None)
        if handler is None:
            await interaction.response.send_message("Request system is unavailable.", ephemeral=True)
            return
        request_id = await handler(
            interaction=interaction,
            satellite_guild_id=self.satellite_guild_id,
            action=self.action,
            reason=self.reason.value.strip(),
        )
        await interaction.response.send_message(f"Request submitted: `#{request_id}`.", ephemeral=True)


class PermissionRequestPromptView(discord.ui.View):
    def __init__(self, bot: discord.Client, satellite_guild_id: int, action: str):
        super().__init__(timeout=180)
        self.bot = bot
        self.satellite_guild_id = satellite_guild_id
        self.action = action

    @discord.ui.button(label="Make Request", style=discord.ButtonStyle.primary)
    async def make_request(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(PermissionRequestModal(self.bot, self.satellite_guild_id, self.action))


class PermissionRequestApprovalView(discord.ui.View):
    def __init__(self, bot: discord.Client, request_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.request_id = request_id

        approve_once = discord.ui.Button(
            label="Approve Once",
            style=discord.ButtonStyle.success,
            custom_id=f"mandy:req:{request_id}:once",
        )
        approve_once.callback = self._approve_once
        self.add_item(approve_once)

        approve_perm = discord.ui.Button(
            label="Perm Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"mandy:req:{request_id}:perm",
        )
        approve_perm.callback = self._approve_perm
        self.add_item(approve_perm)

        deny = discord.ui.Button(
            label="Disapprove",
            style=discord.ButtonStyle.danger,
            custom_id=f"mandy:req:{request_id}:deny",
        )
        deny.callback = self._deny
        self.add_item(deny)

    async def _approve_once(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, "approve_once")

    async def _approve_perm(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, "approve_permanent")

    async def _deny(self, interaction: discord.Interaction) -> None:
        await self._resolve(interaction, "deny")

    async def _resolve(self, interaction: discord.Interaction, resolution: str) -> None:
        handler = getattr(self.bot, "resolve_permission_request", None)
        if handler is None:
            await interaction.response.send_message("Request resolver is unavailable.", ephemeral=True)
            return
        ok, message, finalized = await handler(
            interaction=interaction,
            request_id=self.request_id,
            resolution=resolution,
        )
        if finalized:
            for child in self.children:
                child.disabled = True
            if interaction.message:
                await interaction.message.edit(view=self)
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        if not ok:
            return


class SatelliteDebugView(discord.ui.View):
    def __init__(self, bot: discord.Client, satellite_guild_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.satellite_guild_id = satellite_guild_id

    async def _run_action(self, interaction: discord.Interaction, action: str) -> None:
        handler = getattr(self.bot, "handle_satellite_debug_action", None)
        if handler is None:
            await interaction.response.send_message("Menu handler unavailable.", ephemeral=True)
            return
        await handler(interaction=interaction, satellite_guild_id=self.satellite_guild_id, action=action)

    @discord.ui.button(label="Refresh Dashboard", style=discord.ButtonStyle.secondary)
    async def refresh_dashboard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run_action(interaction, "refresh_dashboard")

    @discord.ui.button(label="Toggle AI Mode", style=discord.ButtonStyle.primary)
    async def toggle_ai_mode(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run_action(interaction, "toggle_ai_mode")

    @discord.ui.button(label="Toggle AI Roast", style=discord.ButtonStyle.primary)
    async def toggle_ai_roast(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run_action(interaction, "toggle_ai_roast")

    @discord.ui.button(label="Test AI API", style=discord.ButtonStyle.success)
    async def test_ai_api(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._run_action(interaction, "test_ai_api")
