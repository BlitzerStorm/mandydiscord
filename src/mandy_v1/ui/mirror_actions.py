from __future__ import annotations

from dataclasses import dataclass

import discord

from mandy_v1.services.logger_service import LoggerService
from mandy_v1.services.mirror_service import MirrorService
from mandy_v1.services.soc_service import SocService
from mandy_v1.services.watcher_service import WatcherService


@dataclass
class MirrorActionContext:
    source_guild_id: int
    source_channel_id: int
    source_message_id: int
    source_author_id: int


class SendTextModal(discord.ui.Modal):
    def __init__(self, title: str, on_submit_fn):
        super().__init__(title=title)
        self.on_submit_fn = on_submit_fn
        self.message_text = discord.ui.TextInput(label="Message", style=discord.TextStyle.paragraph, max_length=1800)
        self.add_item(self.message_text)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.on_submit_fn(interaction, str(self.message_text))


class MirrorActionView(discord.ui.View):
    def __init__(
        self,
        bot: discord.Client,
        ctx: MirrorActionContext,
        mirror_service: MirrorService,
        watcher_service: WatcherService,
        soc_service: SocService,
        logger: LoggerService,
    ) -> None:
        super().__init__(timeout=3600)
        self.bot = bot
        self.ctx = ctx
        self.mirror_service = mirror_service
        self.watcher_service = watcher_service
        self.soc_service = soc_service
        self.logger = logger

    def _allowed(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        if not user:
            return False
        return self.soc_service.can_run(user, 50)

    @discord.ui.button(label="Direct Reply", style=discord.ButtonStyle.primary)
    async def direct_reply(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        async def submit_fn(i: discord.Interaction, text: str) -> None:
            guild = self.bot.get_guild(self.ctx.source_guild_id)
            if not guild:
                await i.response.send_message("Source guild not found.", ephemeral=True)
                return
            channel = guild.get_channel(self.ctx.source_channel_id)
            if not isinstance(channel, discord.TextChannel):
                await i.response.send_message("Source channel not found.", ephemeral=True)
                return
            try:
                source_msg = await channel.fetch_message(self.ctx.source_message_id)
                await source_msg.reply(text)
                self.logger.log("mirror.direct_reply", staff_id=i.user.id, source_message_id=self.ctx.source_message_id)
                await i.response.send_message("Reply sent.", ephemeral=True)
            except discord.HTTPException:
                await i.response.send_message("Failed to reply.", ephemeral=True)

        await interaction.response.send_modal(SendTextModal("Direct Reply", submit_fn))

    @discord.ui.button(label="DM User", style=discord.ButtonStyle.secondary)
    async def dm_user(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return

        async def submit_fn(i: discord.Interaction, text: str) -> None:
            user = self.bot.get_user(self.ctx.source_author_id) or await self.bot.fetch_user(self.ctx.source_author_id)
            if not user:
                await i.response.send_message("User not found.", ephemeral=True)
                return
            try:
                await user.send(text)
                self.logger.log("mirror.dm_user", staff_id=i.user.id, target_user_id=user.id)
                await i.response.send_message("DM sent.", ephemeral=True)
            except discord.HTTPException:
                await i.response.send_message("Failed to send DM.", ephemeral=True)

        await interaction.response.send_modal(SendTextModal("DM User", submit_fn))

    @discord.ui.button(label="Add to Watch List", style=discord.ButtonStyle.success)
    async def add_watch(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        self.watcher_service.add_or_update(self.ctx.source_author_id, threshold=10, response_text="hi|hello|maybe")
        self.logger.log("mirror.add_watch", staff_id=interaction.user.id, target_user_id=self.ctx.source_author_id)
        await interaction.response.send_message("Added watcher with threshold=10.", ephemeral=True)

    @discord.ui.button(label="Ignore", style=discord.ButtonStyle.danger)
    async def ignore(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self._allowed(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        self.mirror_service.ignore_user(self.ctx.source_author_id)
        self.logger.log("mirror.ignore_user", staff_id=interaction.user.id, target_user_id=self.ctx.source_author_id)
        await interaction.response.send_message("User ignored for mirrors.", ephemeral=True)
