from __future__ import annotations

from datetime import timedelta
from typing import Any

import discord

from mandy_v1.config import Settings
from mandy_v1.services.logger_service import LoggerService
from mandy_v1.storage import MessagePackStore


class ServerControlService:
    def __init__(self, settings: Settings, store: MessagePackStore, logger: LoggerService) -> None:
        self.settings = settings
        self.store = store
        self.logger = logger

    async def create_channel(
        self,
        guild: discord.Guild,
        name: str,
        category: discord.CategoryChannel | None = None,
        topic: str | None = None,
        permissions: dict[Any, discord.PermissionOverwrite] | None = None,
    ) -> discord.abc.GuildChannel | None:
        try:
            channel = await guild.create_text_channel(
                str(name or "mandy-room")[:100],
                category=category,
                topic=(str(topic or "")[:1024] or None),
                overwrites=permissions,
                reason="Mandy autonomous server control",
            )
            self.logger.log("server_control.create_channel", guild_id=guild.id, channel_id=channel.id, name=channel.name)
            return channel
        except Exception as exc:  # noqa: BLE001
            self._log_failure("create_channel", guild=guild, error=exc)
            return None

    async def delete_channel(self, guild: discord.Guild, channel: discord.abc.GuildChannel) -> bool:
        try:
            await channel.delete(reason="Mandy autonomous server control")
            self.logger.log("server_control.delete_channel", guild_id=guild.id, channel_id=channel.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("delete_channel", guild=guild, channel=channel, error=exc)
            return False

    async def rename_channel(self, guild: discord.Guild, channel: discord.abc.GuildChannel, new_name: str) -> bool:
        try:
            await channel.edit(name=str(new_name or channel.name)[:100], reason="Mandy autonomous server control")
            self.logger.log("server_control.rename_channel", guild_id=guild.id, channel_id=channel.id, name=str(new_name)[:100])
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("rename_channel", guild=guild, channel=channel, error=exc)
            return False

    async def set_channel_topic(self, guild: discord.Guild, channel: discord.TextChannel, topic: str) -> bool:
        try:
            await channel.edit(topic=str(topic or "")[:1024], reason="Mandy autonomous server control")
            self.logger.log("server_control.set_channel_topic", guild_id=guild.id, channel_id=channel.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("set_channel_topic", guild=guild, channel=channel, error=exc)
            return False

    async def set_slowmode(self, guild: discord.Guild, channel: discord.TextChannel, seconds: int) -> bool:
        try:
            value = max(0, min(21600, int(seconds)))
            await channel.edit(slowmode_delay=value, reason="Mandy autonomous server control")
            self.logger.log("server_control.set_slowmode", guild_id=guild.id, channel_id=channel.id, seconds=value)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("set_slowmode", guild=guild, channel=channel, error=exc)
            return False

    async def lock_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> bool:
        try:
            await channel.set_permissions(guild.default_role, send_messages=False, reason="Mandy autonomous server control")
            self.logger.log("server_control.lock_channel", guild_id=guild.id, channel_id=channel.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("lock_channel", guild=guild, channel=channel, error=exc)
            return False

    async def unlock_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> bool:
        try:
            await channel.set_permissions(guild.default_role, send_messages=None, reason="Mandy autonomous server control")
            self.logger.log("server_control.unlock_channel", guild_id=guild.id, channel_id=channel.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("unlock_channel", guild=guild, channel=channel, error=exc)
            return False

    async def pin_message(self, message: discord.Message) -> bool:
        try:
            await message.pin(reason="Mandy autonomous server control")
            self.logger.log("server_control.pin_message", guild_id=message.guild.id if message.guild else 0, message_id=message.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("pin_message", channel=message.channel, error=exc)
            return False

    async def unpin_message(self, message: discord.Message) -> bool:
        try:
            await message.unpin(reason="Mandy autonomous server control")
            self.logger.log("server_control.unpin_message", guild_id=message.guild.id if message.guild else 0, message_id=message.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("unpin_message", channel=message.channel, error=exc)
            return False

    async def create_role(
        self,
        guild: discord.Guild,
        name: str,
        color: discord.Color | int | None = None,
        permissions: discord.Permissions | None = None,
    ) -> discord.Role | None:
        try:
            role = await guild.create_role(
                name=str(name or "mandy-role")[:100],
                colour=color if isinstance(color, discord.Color) else discord.Color(int(color or 0)),
                permissions=permissions,
                reason="Mandy autonomous server control",
            )
            self.logger.log("server_control.create_role", guild_id=guild.id, role_id=role.id, name=role.name)
            return role
        except Exception as exc:  # noqa: BLE001
            self._log_failure("create_role", guild=guild, error=exc)
            return None

    async def delete_role(self, guild: discord.Guild, role: discord.Role) -> bool:
        try:
            await role.delete(reason="Mandy autonomous server control")
            self.logger.log("server_control.delete_role", guild_id=guild.id, role_id=role.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("delete_role", guild=guild, error=exc)
            return False

    async def assign_role(self, guild: discord.Guild, member: discord.Member, role: discord.Role) -> bool:
        try:
            await member.add_roles(role, reason="Mandy autonomous server control")
            self.logger.log("server_control.assign_role", guild_id=guild.id, user_id=member.id, role_id=role.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("assign_role", guild=guild, error=exc)
            return False

    async def remove_role(self, guild: discord.Guild, member: discord.Member, role: discord.Role) -> bool:
        try:
            await member.remove_roles(role, reason="Mandy autonomous server control")
            self.logger.log("server_control.remove_role", guild_id=guild.id, user_id=member.id, role_id=role.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("remove_role", guild=guild, error=exc)
            return False

    async def rename_role(self, guild: discord.Guild, role: discord.Role, new_name: str) -> bool:
        try:
            await role.edit(name=str(new_name or role.name)[:100], reason="Mandy autonomous server control")
            self.logger.log("server_control.rename_role", guild_id=guild.id, role_id=role.id, name=str(new_name)[:100])
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("rename_role", guild=guild, error=exc)
            return False

    async def nickname_member(self, guild: discord.Guild, member: discord.Member, nick: str) -> bool:
        try:
            await member.edit(nick=str(nick or "")[:32], reason="Mandy autonomous server control")
            self.logger.log("server_control.nickname_member", guild_id=guild.id, user_id=member.id, nick=str(nick)[:32])
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("nickname_member", guild=guild, error=exc)
            return False

    async def kick_member(self, guild: discord.Guild, member: discord.Member, reason: str) -> bool:
        try:
            if int(member.id) == int(self.settings.god_user_id):
                return False
            await member.kick(reason=str(reason or "Mandy autonomous server control")[:240])
            self.logger.log("server_control.kick_member", guild_id=guild.id, user_id=member.id)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("kick_member", guild=guild, error=exc)
            return False

    async def timeout_member(self, guild: discord.Guild, member: discord.Member, duration_minutes: int, reason: str) -> bool:
        try:
            if int(member.id) == int(self.settings.god_user_id):
                return False
            until = discord.utils.utcnow() + timedelta(minutes=max(1, min(40320, int(duration_minutes))))
            await member.timeout(until, reason=str(reason or "Mandy autonomous server control")[:240])
            self.logger.log("server_control.timeout_member", guild_id=guild.id, user_id=member.id, minutes=int(duration_minutes))
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("timeout_member", guild=guild, error=exc)
            return False

    async def bulk_delete(self, guild: discord.Guild, channel: discord.TextChannel, limit: int) -> int:
        try:
            deleted = await channel.purge(limit=max(1, min(200, int(limit))), bulk=True, reason="Mandy autonomous server control")
            count = len(deleted) if isinstance(deleted, list) else 0
            self.logger.log("server_control.bulk_delete", guild_id=guild.id, channel_id=channel.id, deleted=count)
            return count
        except Exception as exc:  # noqa: BLE001
            self._log_failure("bulk_delete", guild=guild, channel=channel, error=exc)
            return 0

    async def send_as_mandy(self, channel: discord.abc.Messageable, content: str) -> discord.Message | None:
        try:
            sent = await channel.send(str(content or "")[:1900])
            self.logger.log("server_control.send_as_mandy", channel_id=getattr(channel, "id", 0), chars=len(str(content or "")))
            return sent
        except Exception as exc:  # noqa: BLE001
            self._log_failure("send_as_mandy", channel=channel, error=exc)
            return None

    async def send_embed(
        self,
        channel: discord.abc.Messageable,
        title: str,
        description: str,
        color: int,
        fields: list[dict[str, Any]] | None = None,
    ) -> discord.Message | None:
        try:
            embed = discord.Embed(title=str(title or "")[:256], description=str(description or "")[:4096], color=int(color or 0))
            for row in fields or []:
                if not isinstance(row, dict):
                    continue
                embed.add_field(
                    name=str(row.get("name", ""))[:256] or "Field",
                    value=str(row.get("value", ""))[:1024] or "-",
                    inline=bool(row.get("inline", False)),
                )
            sent = await channel.send(embed=embed)
            self.logger.log("server_control.send_embed", channel_id=getattr(channel, "id", 0), title=embed.title)
            return sent
        except Exception as exc:  # noqa: BLE001
            self._log_failure("send_embed", channel=channel, error=exc)
            return None

    async def react(self, message: discord.Message, emoji: str) -> bool:
        try:
            await message.add_reaction(str(emoji or "✨")[:32])
            self.logger.log("server_control.react", message_id=message.id, emoji=str(emoji or "✨")[:32])
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("react", channel=message.channel, error=exc)
            return False

    async def set_server_name(self, guild: discord.Guild, name: str) -> bool:
        try:
            await guild.edit(name=str(name or guild.name)[:100], reason="Mandy autonomous server control")
            self.logger.log("server_control.set_server_name", guild_id=guild.id, name=str(name)[:100])
            return True
        except Exception as exc:  # noqa: BLE001
            self._log_failure("set_server_name", guild=guild, error=exc)
            return False

    async def create_invite(self, guild: discord.Guild, channel: discord.TextChannel | None = None, max_age_hours: int = 24) -> str:
        try:
            target = channel
            if target is None:
                me = guild.me
                if me is None:
                    return ""
                target = next(
                    (
                        text_channel
                        for text_channel in guild.text_channels
                        if text_channel.permissions_for(me).create_instant_invite
                        and text_channel.permissions_for(me).view_channel
                    ),
                    None,
                )
            if target is None:
                return ""
            invite = await target.create_invite(
                max_age=max(0, min(7 * 24, int(max_age_hours))) * 3600,
                max_uses=0,
                unique=True,
                reason="Mandy autonomous server control",
            )
            self.logger.log("server_control.create_invite", guild_id=guild.id, channel_id=target.id, url=invite.url)
            return str(invite.url)
        except Exception as exc:  # noqa: BLE001
            self._log_failure("create_invite", guild=guild, channel=channel, error=exc)
            return ""

    async def get_member_list(self, guild: discord.Guild) -> list[dict[str, Any]]:
        try:
            return [{"id": member.id, "name": member.display_name} for member in guild.members]
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_member_list", guild=guild, error=exc)
            return []

    async def get_channel_list(self, guild: discord.Guild) -> list[dict[str, Any]]:
        try:
            return [{"id": channel.id, "name": channel.name, "type": str(channel.type)} for channel in guild.channels]
        except Exception as exc:  # noqa: BLE001
            self._log_failure("get_channel_list", guild=guild, error=exc)
            return []

    async def dispatch_action(
        self,
        guild: discord.Guild,
        payload: dict[str, Any],
        *,
        source_message: discord.Message | None = None,
    ) -> bool:
        try:
            action = str(payload.get("action", "")).strip()
            if not action:
                return False
            if action == "nickname_member":
                target = guild.get_member(int(payload.get("target", 0) or 0))
                return bool(target and await self.nickname_member(guild, target, str(payload.get("value", "")).strip()))
            if action == "create_channel":
                category_name = str(payload.get("category", "")).strip()
                category = discord.utils.get(guild.categories, name=category_name) if category_name else None
                return bool(
                    await self.create_channel(
                        guild,
                        str(payload.get("name", "")).strip(),
                        category=category,
                        topic=str(payload.get("topic", "")).strip(),
                    )
                )
            if action == "pin_message" and source_message is not None:
                return await self.pin_message(source_message)
            if action == "set_slowmode":
                channel = guild.get_channel(int(payload.get("channel_id", 0) or 0))
                return bool(
                    isinstance(channel, discord.TextChannel)
                    and await self.set_slowmode(guild, channel, int(payload.get("seconds", 0) or 0))
                )
            if action == "rename_channel":
                channel = guild.get_channel(int(payload.get("channel_id", 0) or 0))
                return bool(channel and await self.rename_channel(guild, channel, str(payload.get("value", "")).strip()))
            if action == "lock_channel" and source_message is not None and isinstance(source_message.channel, discord.TextChannel):
                return await self.lock_channel(guild, source_message.channel)
            if action == "unlock_channel" and source_message is not None and isinstance(source_message.channel, discord.TextChannel):
                return await self.unlock_channel(guild, source_message.channel)
            if action == "set_channel_topic":
                channel = guild.get_channel(int(payload.get("channel_id", 0) or 0))
                return bool(
                    isinstance(channel, discord.TextChannel)
                    and await self.set_channel_topic(guild, channel, str(payload.get("value", "")).strip())
                )
            if action == "delete_channel":
                channel = guild.get_channel(int(payload.get("channel_id", 0) or 0))
                return bool(channel and await self.delete_channel(guild, channel))
            if action == "create_role":
                return bool(await self.create_role(guild, str(payload.get("name", "")).strip()))
            if action == "delete_role":
                role = guild.get_role(int(payload.get("target", 0) or 0))
                return bool(role and await self.delete_role(guild, role))
            if action == "assign_role":
                member = guild.get_member(int(payload.get("target", 0) or 0))
                role = guild.get_role(int(payload.get("role_id", 0) or 0))
                return bool(member and role and await self.assign_role(guild, member, role))
            if action == "remove_role":
                member = guild.get_member(int(payload.get("target", 0) or 0))
                role = guild.get_role(int(payload.get("role_id", 0) or 0))
                return bool(member and role and await self.remove_role(guild, member, role))
            if action == "rename_role":
                role = guild.get_role(int(payload.get("target", 0) or 0))
                return bool(role and await self.rename_role(guild, role, str(payload.get("value", "")).strip()))
            if action == "set_server_name":
                return await self.set_server_name(guild, str(payload.get("value", "")).strip())
            if action == "bulk_delete" and source_message is not None and isinstance(source_message.channel, discord.TextChannel):
                return (await self.bulk_delete(guild, source_message.channel, int(payload.get("limit", 0) or 0))) > 0
            if action == "timeout_member":
                member = guild.get_member(int(payload.get("target", 0) or 0))
                return bool(
                    member
                    and await self.timeout_member(
                        guild,
                        member,
                        int(payload.get("duration_minutes", 10) or 10),
                        str(payload.get("reason", "")).strip(),
                    )
                )
            if action == "kick_member":
                member = guild.get_member(int(payload.get("target", 0) or 0))
                return bool(member and await self.kick_member(guild, member, str(payload.get("reason", "")).strip()))
            return False
        except Exception as exc:  # noqa: BLE001
            self._log_failure("dispatch_action", guild=guild, error=exc)
            return False

    def _log_failure(
        self,
        action: str,
        *,
        guild: discord.Guild | None = None,
        channel: Any | None = None,
        error: Exception,
    ) -> None:
        self.logger.log(
            "server_control.failed",
            action=action,
            guild_id=guild.id if guild else 0,
            channel_id=getattr(channel, "id", 0) if channel else 0,
            error=str(error)[:240],
        )
