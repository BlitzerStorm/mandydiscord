from __future__ import annotations

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


class DMBridgeService:
    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger

    async def ensure_channel(self, bot: discord.Client, user: discord.abc.User) -> discord.TextChannel | None:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return None
        category = discord.utils.get(admin_guild.categories, name="ENGINEERING")
        if category is None:
            category = await admin_guild.create_category("ENGINEERING", reason="Mandy v1 DM bridge setup")
        channel_name = f"dm-{user.id}"
        channel = discord.utils.get(category.text_channels, name=channel_name)
        if channel is None:
            channel = await category.create_text_channel(channel_name, reason="Mandy v1 DM bridge opened")
            await channel.send(f"DM bridge opened for <@{user.id}> (`{user.id}`)")
        self.store.data["dm_bridges"][str(user.id)] = {"channel_id": channel.id, "active": True}
        self.store.touch()
        return channel

    async def relay_inbound(self, bot: discord.Client, message: discord.Message) -> None:
        channel = await self.ensure_channel(bot, message.author)
        if not channel:
            return
        attachments = " ".join(a.url for a in message.attachments)
        text = message.content or "(no text)"
        if attachments:
            text = f"{text}\n{attachments}"
        await channel.send(f"[INBOUND] <@{message.author.id}>: {text}")
        self.logger.log("dm_bridge.inbound", user_id=message.author.id)

    async def relay_outbound(self, bot: discord.Client, message: discord.Message) -> bool:
        if not isinstance(message.channel, discord.TextChannel):
            return False
        if not message.channel.name.startswith("dm-"):
            return False
        parts = message.channel.name.split("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            return False
        user_id = int(parts[1])
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        if not user:
            return False
        await user.send(message.content)
        self.logger.log("dm_bridge.outbound", user_id=user_id, source_channel_id=message.channel.id)
        return True
