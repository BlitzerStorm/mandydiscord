from __future__ import annotations

from typing import List, Optional

import discord

class RateLimitView(discord.ui.View):
    def __init__(self, cog, job_id: str, user_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.job_id = job_id
        self.user_id = user_id

    @discord.ui.button(label="WAIT", style=discord.ButtonStyle.primary)
    async def wait_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await self.cog.accept_job(self.job_id)
        try:
            await interaction.response.edit_message(content="Queued. Will retry automatically.", view=None)
        except (discord.NotFound, discord.HTTPException):
            return
        finally:
            self.stop()

    @discord.ui.button(label="CANCEL", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await self.cog.cancel_job(self.job_id)
        try:
            await interaction.response.edit_message(content="Cancelled.", view=None)
        except (discord.NotFound, discord.HTTPException):
            return
        finally:
            self.stop()

class ConfirmView(discord.ui.View):
    def __init__(self, cog, user_id: int, channel_id: int, query: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.channel_id = channel_id
        self.query = query

    @discord.ui.button(label="CONFIRM", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await interaction.response.edit_message(content="Confirmed. Processing...", view=None)
        await self.cog.confirm_request(self.user_id, self.channel_id, self.query)

    @discord.ui.button(label="CANCEL", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await interaction.response.edit_message(content="Cancelled.", view=None)

    @discord.ui.button(label="WAIT", style=discord.ButtonStyle.secondary)
    async def wait_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Not authorized.", ephemeral=True)
        await interaction.response.send_message("Waiting. Use CONFIRM when ready.", ephemeral=True)

class UserPickView(discord.ui.View):
    def __init__(self, cog, requester_id: int, action: Dict[str, Any], candidates: List[Tuple[int, str]]):
        super().__init__(timeout=120)
        self.cog = cog
        self.requester_id = requester_id
        self.action = action
        for idx, (uid, label) in enumerate(candidates[:5], start=1):
            btn = discord.ui.Button(label=f"{idx}. {label}", style=discord.ButtonStyle.secondary)
            async def callback(interaction: discord.Interaction, picked_id: int = uid, picked_label: str = label):
                if interaction.user.id != self.requester_id:
                    return await interaction.response.send_message("Not authorized.", ephemeral=True)
                await interaction.response.edit_message(
                    content=f"Selected {picked_label}. Processing...",
                    view=None,
                )
                await self.cog.handle_user_pick(self.action, picked_id, interaction.channel, interaction.guild, interaction.user)
            btn.callback = callback
            self.add_item(btn)
        cancel = discord.ui.Button(label="CANCEL", style=discord.ButtonStyle.danger)
        async def cancel_callback(interaction: discord.Interaction):
            if interaction.user.id != self.requester_id:
                return await interaction.response.send_message("Not authorized.", ephemeral=True)
            await interaction.response.edit_message(content="Cancelled.", view=None)
        cancel.callback = cancel_callback
        self.add_item(cancel)

