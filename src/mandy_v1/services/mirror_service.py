from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore

SUPER_USER_ID = 741470965359443970


@dataclass
class SourceRef:
    guild_id: int
    channel_id: int
    message_id: int
    author_id: int


class MirrorService:
    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger
        self.recent_by_user: dict[int, deque[str]] = defaultdict(lambda: deque(maxlen=50))
        self.in_memory_map: dict[int, SourceRef] = {}

    def is_ignored(self, user_id: int) -> bool:
        return user_id in set(self.store.data["mirrors"].get("ignored_user_ids", []))

    def ignore_user(self, user_id: int) -> None:
        ids = set(self.store.data["mirrors"].get("ignored_user_ids", []))
        ids.add(user_id)
        self.store.data["mirrors"]["ignored_user_ids"] = sorted(ids)
        self.store.touch()

    async def ensure_satellite(self, bot: discord.Client, satellite_guild: discord.Guild) -> dict[str, int] | None:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild:
            return None
        if satellite_guild.id == admin_guild.id:
            return None
        category_name = f"SATELLITES / Active / {satellite_guild.name}"[:95]
        category = discord.utils.get(admin_guild.categories, name=category_name)
        if category is None:
            category = await admin_guild.create_category(category_name, reason="Mandy v1 satellite setup")
        mirror_feed = discord.utils.get(category.text_channels, name="mirror-feed")
        if mirror_feed is None:
            mirror_feed = await category.create_text_channel("mirror-feed", reason="Mandy v1 mirror feed")
        debug_channel = discord.utils.get(category.text_channels, name="debug")
        if debug_channel is None:
            debug_channel = await category.create_text_channel("debug", reason="Mandy v1 debug channel")

        server_role_name = self.role_name_for_server(satellite_guild.id)
        server_role = discord.utils.get(admin_guild.roles, name=server_role_name)
        if server_role is None:
            server_role = await admin_guild.create_role(name=server_role_name, mentionable=False, reason="Mandy v1 SOC role")
        await mirror_feed.set_permissions(admin_guild.default_role, view_channel=False)
        await mirror_feed.set_permissions(server_role, view_channel=True, send_messages=False, read_message_history=True)
        await debug_channel.set_permissions(admin_guild.default_role, view_channel=False)
        await debug_channel.set_permissions(server_role, view_channel=True, send_messages=False, read_message_history=True)

        existing = self.store.data["mirrors"]["servers"].get(str(satellite_guild.id), {})
        payload = {
            "category_id": category.id,
            "mirror_feed_id": mirror_feed.id,
            "debug_channel_id": debug_channel.id,
            "debug_dashboard_message_id": int(existing.get("debug_dashboard_message_id", 0) or 0),
            "satellite_invite_url": str(existing.get("satellite_invite_url", "")),
        }
        self.store.data["mirrors"]["servers"][str(satellite_guild.id)] = payload
        self.store.touch()
        self.logger.log(
            "mirror.satellite_ready",
            satellite_guild_id=satellite_guild.id,
            mirror_feed_id=mirror_feed.id,
            debug_channel_id=debug_channel.id,
        )
        return payload

    def role_name_for_server(self, guild_id: int) -> str:
        return f"SOC:SERVER:{guild_id}"

    async def sync_admin_member_access(
        self,
        bot: discord.Client,
        member: discord.Member,
        bypass_user_ids: set[int],
    ) -> None:
        admin_guild = bot.get_guild(self.settings.admin_guild_id)
        if not admin_guild or member.guild.id != admin_guild.id:
            return
        if member.bot:
            return
        roles_to_add: list[discord.Role] = []
        for guild_id in self.store.data["mirrors"]["servers"].keys():
            satellite = bot.get_guild(int(guild_id))
            if not satellite:
                continue
            in_satellite = satellite.get_member(member.id) is not None
            allow = in_satellite or (member.id in bypass_user_ids) or (member.id == SUPER_USER_ID)
            if not allow:
                continue
            role = discord.utils.get(admin_guild.roles, name=self.role_name_for_server(int(guild_id)))
            if role and role not in member.roles:
                roles_to_add.append(role)
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Mandy v1 SOC access sync")

    async def mirror_message(
        self,
        bot: discord.Client,
        message: discord.Message,
        view_factory: Any,
    ) -> None:
        if not message.guild:
            return
        if message.guild.id == self.settings.admin_guild_id:
            return
        if message.author.bot or self.is_ignored(message.author.id):
            return
        server_cfg = self.store.data["mirrors"]["servers"].get(str(message.guild.id))
        if not server_cfg:
            server_cfg = await self.ensure_satellite(bot, message.guild)
            if not server_cfg:
                return
        target = bot.get_channel(int(server_cfg["mirror_feed_id"]))
        if not isinstance(target, discord.TextChannel):
            return

        content = message.content.strip() or "(no text)"
        attachment_urls = [a.url for a in message.attachments]
        history_line = content
        if attachment_urls:
            history_line += " | attachments: " + ", ".join(attachment_urls)
        self.recent_by_user[message.author.id].append(history_line)
        recent = list(self.recent_by_user[message.author.id])
        preview = "\n".join(f"- {line}" for line in recent[-5:])

        embed = discord.Embed(title=f"Mirror: {message.guild.name}", description=content[:3500], color=0x1F8B4C)
        embed.set_author(name=f"{message.author} ({message.author.id})")
        embed.add_field(name="Source", value=f"<#{message.channel.id}> / `{message.channel.id}`", inline=False)
        embed.add_field(name="Recent Activity (last 50)", value=preview[:1000] if preview else "(none yet)", inline=False)
        if attachment_urls:
            embed.add_field(name="Attachments", value="\n".join(attachment_urls)[:1000], inline=False)
        embed.set_footer(text=f"Source Message ID: {message.id}")

        mirrored = await target.send(embed=embed, view=view_factory(message))
        self.in_memory_map[mirrored.id] = SourceRef(
            guild_id=message.guild.id,
            channel_id=message.channel.id,
            message_id=message.id,
            author_id=message.author.id,
        )
        self.logger.log("mirror.message", source_guild_id=message.guild.id, source_message_id=message.id, mirrored_message_id=mirrored.id)

    async def forward_reaction(
        self,
        bot: discord.Client,
        reaction: discord.Reaction,
        user: discord.abc.User,
    ) -> None:
        if user.bot:
            return
        ref = self.in_memory_map.get(reaction.message.id)
        if not ref:
            return
        guild = bot.get_guild(ref.guild_id)
        if not guild:
            return
        channel = guild.get_channel(ref.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            source_msg = await channel.fetch_message(ref.message_id)
            await source_msg.add_reaction(str(reaction.emoji))
            self.logger.log("mirror.reaction_forwarded", mirrored_message_id=reaction.message.id, source_message_id=ref.message_id)
        except discord.HTTPException:
            self.logger.log("mirror.reaction_forward_failed", mirrored_message_id=reaction.message.id, source_message_id=ref.message_id)
